"""Import of the commercial "Cards" workbook into one board of one company database (preview and atomic apply).

Not the Bid report importer (bid_import_*.py): a different format and a different ledger. This module reads nothing
from disk and takes no decision about who may import; the route authorizes first and passes in the caller's context,
the board id, the open company session and the people the central database lets this company assign.

Rules (approved):
- Blank `card_id` creates a card. A filled `card_id` updates that card, only if it is a card of THIS board; any other
  id (missing, another board, another company) gets the same "card_not_found" row error, so nothing is revealed.
  A card_id repeated in the file rejects every row that uses it.
- Opt-in (`create_unmatched`, the caller's explicit choice, off by default): a filled `card_id` that is not a card of
  this board makes the row a new card instead of an error, with a "source_id_new_card" warning in the preview. The id
  is never kept: the new card gets its own id from this database, and a card_id that does match a card of this board
  still updates that card. Missing, other-board and other-company ids all behave the same, so nothing is revealed. It
  is an explicit choice because a mistyped or stale id in an update workbook would otherwise create a duplicate card.
  The choice is part of the plan digest, so a confirmation made with a different choice than its preview is refused.
- For an update, every contract column present in the file is authoritative: a blank cell clears a nullable field
  (deal_value_next_year_usd becomes 0), except `stage`, where blank keeps the card where it is. Columns absent from the
  file leave their fields untouched. `deal_year` and `deal_total_usd` are never written. A new card with a blank
  stage goes to the board's first stage.
- All-or-nothing: preview reports every row; confirmation is refused while any row is invalid, and applies all creates
  and updates in one transaction (plus its ledger row) or none.
- Required column: `card_id` must be a column of the workbook (the reader rejects the file otherwise), so an update
  workbook can never be read as a create-only one.
- Duplicate protection: the fingerprint is the SHA-256 of the normalized, validated content (not the filename, the
  file bytes, row order, `deal_year` or `deal_total_usd`, nor the presence of those two columns) scoped to company +
  board. A logical workbook, create or update, is applied at most once per board, even if the board changed since
  (A, then B, then A again is refused). It is checked under the write lock before any card is touched and enforced by a
  unique index on the ledger. The ledger is permanent: it is not purged when cards are deleted, so a workbook whose
  cards were deleted cannot be re-applied unchanged; it must differ in some business value.
- The plan digest binds a confirmation to what was previewed: the workbook content plus the state of every card it
  would touch. If either changed in between, the confirmation is refused (409) and a new preview is needed.
- Recovery (`recover_missing`, the caller's explicit choice, off by default): the at-most-once ledger refuses a workbook
  whose logical content was applied before, even when its cards were deleted since. Recovery is the one narrow exception:
  it applies only when that ledger refusal is what blocks the ordinary import, and it writes ONLY the rows whose filled
  card_id is not a card of this board (new cards with fresh ids, as with `create_unmatched`). Every other row is skipped
  without a write: rows whose card_id is a card of this board (never updated or duplicated), rows with an empty
  card_id (they would only duplicate what the earlier import created) and rows whose source id was already restored by
  an earlier import or recovery whose card still exists here (the ledger records source id -> new id). Entries written
  before that record existed are handled conservatively: while any card they created still exists on the board,
  recovery is refused. The ledger row of a recovery has its own repeat_guard = SHA-256(fingerprint, "recovery", the
  highest card id ever allocated), so two confirmations of one preview cannot both apply, a completed recovery cannot be
  replayed (the highest id moved), and a deliberate later recovery after deleting the restored cards is possible. The
  mode, the eligible rows and that high-water mark are part of the plan digest. Existing ledger rows are never changed.
Row messages and refusals never contain cell values or internal exceptions.
"""
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import excel_import_ledger as ledger
from card_rules import apply_card_updates, audit_new_card_owner, new_card_fields
from commercial_excel_reader import CommercialReadResult, ExcelIssue
from lead_options import DEAL_CURRENCY
from models import Card, CardStageEvent, Column, CompanyUser
from product_catalog import catalog_names

FINGERPRINT_VERSION = 2  # 2: any import is guarded; informational columns left out; ledger rows of v1 do not match
CARD_LOOKUP_CHUNK = 500

# (record field, Card attribute); `stage` is handled apart.
FIELD_MAP = (
    ("title", "title"),
    ("company", "company"),
    ("contact", "contact"),
    ("details", "details"),
    ("deal_value_usd", "deal_value"),
    ("deal_value_next_year_usd", "deal_value_next_year"),
    ("confidence_level", "confidence_level"),
    ("expected_close_date", "expected_close_date"),
    ("product_line", "product_line"),
    ("lead_source", "lead_source"),
    ("salesperson_email", "salesperson_id"),
)

# Informational columns: never written, so neither their content nor their presence identifies a workbook.
NON_FINGERPRINT_COLUMNS = frozenset({"deal_year", "deal_total_usd"})

CREATE = "create"
UPDATE = "update"
UNCHANGED = "unchanged"
INVALID = "invalid"
SKIPPED = "skipped"  # recovery only: a row that is deliberately not written (see skip_reason)


class ImportRefused(Exception):
    """A refusal the route answers with `status` and a {code, message} detail. Carries no cell content."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


@dataclass
class RowPlan:
    row_number: int
    action: str
    card_id: int | None = None
    title: str | None = None
    stage: str | None = None
    column_id: int | None = None
    values: dict = field(default_factory=dict)    # Card attribute -> value to write (create: all, update: changed)
    changed: list = field(default_factory=list)   # update: names of the fields that change (incl. "stage")
    diffs: list = field(default_factory=list)     # update: {field, old, new} for every changed field, display-ready
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    unmatched_id: bool = False                    # create: the row named a card_id that is not on this board
    skip_reason: str | None = None                # recovery, action 'skipped': exists | empty_id | already_restored


@dataclass
class Plan:
    rows: list
    cards: dict                       # card_id -> Card, the cards this plan updates
    fingerprint: str
    digest: str
    columns: list                     # the board's columns, by position
    blocked_by: ImportRefused | None = None
    previous: object = None           # latest applied import of this logical workbook on this board, if any
    create_unmatched: bool = False    # the caller's choice for ids that are not cards of this board
    recovery: bool = False            # this plan is a recovery of missing cards (see the module notes)
    repeat_guard: str | None = None   # the ledger guard of this plan (None: the workbook fingerprint)
    recovery_info: dict | None = None # what recovery would do, reported with the preview when the ledger blocks the import
    recovery_refusal: ImportRefused | None = None
    restored_from: dict = field(default_factory=dict)  # recovery: source id -> new id (filled when applied)

    def count(self, action: str) -> int:
        return sum(1 for row in self.rows if row.action == action)

    @property
    def confirmable(self) -> bool:
        return self.refusal() is None

    def refusal(self) -> ImportRefused | None:
        if self.blocked_by:  # first: a workbook already applied is refused whatever the board looks like now
            return self.blocked_by
        if self.recovery_refusal:
            return self.recovery_refusal
        if self.count(INVALID):
            return ImportRefused(422, "validation_errors", "Fix the rows with errors; nothing was imported")
        if self.recovery and not self.count(CREATE):
            return ImportRefused(409, "nothing_to_recover", "No card of this workbook is missing from this board; nothing was imported")
        if not self.count(CREATE) and not self.count(UPDATE):
            return ImportRefused(409, "no_changes", "The workbook changes nothing on this board")
        return None


def issue_dict(issue: ExcelIssue) -> dict:
    return {"row": issue.row, "column": issue.column, "code": issue.code, "message": issue.message}


def _row_issue(row: int, column: str | None, code: str, message: str) -> dict:
    return {"row": row, "column": column, "code": code, "message": message}


def _money(value) -> Decimal | None:
    return None if value is None else Decimal(str(round(float(value), 2)))


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _same(attr: str, current, new) -> bool:
    if attr in ("deal_value", "deal_value_next_year"):
        return _money(current) == _money(new)
    return current == new


def _show(attr: str, value, emails: dict[int, str]):
    """A value as the preview shows it: text, or None for empty/unassigned. Money has two decimals."""
    if value is None:
        return None
    if attr in ("deal_value", "deal_value_next_year"):
        return f"{_money(value):.2f}"
    if attr == "salesperson_id":
        return emails.get(value, "(a user who can no longer be assigned)")
    return str(value)


def _canonical_record(record) -> dict:
    def text(value):
        return None if value is None else value.casefold()

    return {
        "card_id": record.card_id,
        "title": record.title, "company": record.company, "contact": record.contact, "details": record.details,
        "deal_value_usd": None if record.deal_value_usd is None else str(record.deal_value_usd),
        "deal_value_next_year_usd": str(record.deal_value_next_year_usd),
        "confidence_level": record.confidence_level,
        "expected_close_date": None if record.expected_close_date is None else record.expected_close_date.isoformat(),
        "product_line": text(record.product_line), "lead_source": record.lead_source,
        "salesperson_email": record.salesperson_email, "stage": text(record.stage),
    }


def fingerprint(company_slug: str, board_id: int, read: CommercialReadResult) -> str:
    """Deterministic over the logical content: row order, filename, deal_year and deal_total_usd (and whether those
    two columns exist) do not count. Which other columns exist does, because an absent column leaves a field alone while
    a present blank one clears it."""
    rows = sorted(json.dumps(_canonical_record(row.record), sort_keys=True, ensure_ascii=True) for row in read.rows)
    body = json.dumps(
        {"v": FINGERPRINT_VERSION, "company": company_slug, "board": board_id, "columns": sorted(set(read.columns) - NON_FINGERPRINT_COLUMNS),
         "rows": rows},
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def high_water(db: Session) -> int:
    """The highest card id ever allocated in this database (cards and stage-event history), as stage_history allocates."""
    return max(db.query(func.max(Card.id)).scalar() or 0, db.query(func.max(CardStageEvent.card_id)).scalar() or 0)


def _digest(fp: str, rows: list[RowPlan], cards: dict, create_unmatched: bool = False, recovery: str | None = None) -> str:
    state = [
        [r.row_number, r.action, r.card_id, sorted(r.changed), r.column_id,
         cards[r.card_id].updated_at.isoformat() if r.card_id in cards and cards[r.card_id].updated_at else None]
        for r in rows
    ]
    body = [fp, state] if not create_unmatched else [fp, state, "create_unmatched"]  # unchanged digest when the option is off
    if recovery is not None:
        body.append(["recovery", recovery])  # the mode, and the highest card id at the time: stale once a card is allocated
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _load_cards(db: Session, board_id: int, ids: list[int]) -> dict[int, Card]:
    found: dict[int, Card] = {}
    for start in range(0, len(ids), CARD_LOOKUP_CHUNK):
        chunk = ids[start:start + CARD_LOOKUP_CHUNK]
        for card in db.query(Card).join(Column).filter(Column.board_id == board_id, Card.id.in_(chunk)).all():
            found[card.id] = card
    return found


def _classify(
    db: Session, board_id: int, read: CommercialReadResult, people: dict[str, int], create_unmatched: bool,
) -> tuple[list, dict, list]:
    """Validate every row against this company's board and classify it: (row plans, the existing cards the rows name,
    the board's columns). Reads only; `people` maps the lower-cased e-mail of each person the central database lets this
    company assign to their canonical user id. `create_unmatched`: see the module notes."""
    columns = db.query(Column).filter(Column.board_id == board_id).order_by(Column.position, Column.id).all()
    stages: dict[str, list] = {}
    for column in columns:
        stages.setdefault(column.title.casefold(), []).append(column)
    catalog = {name.casefold(): name for name in catalog_names(db)}
    local_users = {user_id for (user_id,) in db.query(CompanyUser.id)}
    present = set(read.columns)
    emails = {user_id: email for email, user_id in people.items()}

    uses: dict[int, int] = {}
    for row in read.rows:
        if row.record.card_id is not None:
            uses[row.record.card_id] = uses.get(row.record.card_id, 0) + 1
    cards = _load_cards(db, board_id, sorted(uses))

    plans: list[RowPlan] = []
    for row in read.rows:
        rec, number = row.record, row.row_number
        plan = RowPlan(number, INVALID, card_id=rec.card_id, title=rec.title)
        plan.errors = [issue_dict(issue) for issue in row.errors]
        card = None
        if rec.card_id is not None:
            card = cards.get(rec.card_id)
            if uses[rec.card_id] > 1:
                plan.errors.append(_row_issue(number, "card_id", "duplicate_card_id", "This card_id appears more than once in the file"))
            elif card is None and not create_unmatched:
                plan.errors.append(_row_issue(number, "card_id", "card_not_found", "No card with this card_id exists on this board"))
            elif card is None:
                plan.unmatched_id = True
                plan.warnings.append(_row_issue(
                    number, "card_id", "source_id_new_card",
                    "No card with this card_id exists on this board: a new card is created, with its own id"))

        target = None
        if rec.stage and "stage" in present:
            matches = stages.get(rec.stage.casefold(), [])
            if len(matches) == 1:
                target = matches[0]
            else:
                code = "unknown_stage" if not matches else "ambiguous_stage"
                plan.errors.append(_row_issue(number, "stage", code, "Stage is not a column title of this board" if not matches
                                              else "Several columns of this board have this title"))
        elif card is None and (rec.card_id is None or create_unmatched):
            if columns:
                target = columns[0]
                if "stage" in present:
                    plan.warnings.append(_row_issue(number, "stage", "stage_defaulted", f'Empty stage: the card goes to "{target.title}"'))
            else:
                plan.errors.append(_row_issue(number, "stage", "board_has_no_stages", "The board has no stages"))
        elif card is not None:
            target = card.column

        if target is not None and target.stage_kind == "lost" and (card is None or target.id != card.column_id):
            # An entry into LOST (a new card in it, or a card moved into it) needs a reason, which a workbook has no
            # column for: do it on the board.
            plan.errors.append(_row_issue(number, "stage", "loss_reason_required",
                                          "A card entering the lost stage needs a reason; create or move it on the board instead"))

        resolved: dict = {}
        if rec.product_line is not None and "product_line" in present:
            name = catalog.get(rec.product_line.casefold())
            if name is None and card is not None and (card.product_line or "").casefold() == rec.product_line.casefold():
                name = card.product_line  # a legacy product the card already carries stays as it is
            if name is None:
                plan.errors.append(_row_issue(number, "product_line", "unknown_product_line", "Product line is not in this company's catalog"))
            resolved["product_line"] = name
        if rec.salesperson_email is not None and "salesperson_email" in present:
            user_id = people.get(rec.salesperson_email)
            if user_id is None or user_id not in local_users:
                plan.errors.append(_row_issue(number, "salesperson_email", "salesperson_unavailable",
                                              "No salesperson with this e-mail can be assigned in this company"))
            resolved["salesperson_id"] = user_id

        if plan.errors:
            plans.append(plan)
            continue

        values = {}
        for spec_field, attr in FIELD_MAP:
            if spec_field not in present:
                continue
            if attr in resolved:
                values[attr] = resolved[attr]
            elif attr == "salesperson_id" or attr == "product_line":
                values[attr] = None
            elif attr in ("deal_value", "deal_value_next_year"):
                values[attr] = _float(getattr(rec, spec_field))
            else:
                values[attr] = getattr(rec, spec_field)
        plan.stage = target.title if target else None
        plan.column_id = target.id if target else None
        if card is None:
            plan.action, plan.values = CREATE, values
        else:
            plan.values = {attr: value for attr, value in values.items() if not _same(attr, getattr(card, attr), value)}
            plan.changed = sorted(plan.values)
            plan.diffs = [{"field": attr, "old": _show(attr, getattr(card, attr), emails),
                           "new": _show(attr, plan.values[attr], emails)} for attr in plan.changed]
            if target is not None and target.id != card.column_id:
                plan.changed.append("stage")
                plan.diffs.append({"field": "stage", "old": card.column.title, "new": target.title})
            plan.action = UPDATE if plan.changed else UNCHANGED
        plans.append(plan)

    return plans, cards, columns


def build_plan(
    db: Session, board_id: int, company_slug: str, read: CommercialReadResult, people: dict[str, int],
    create_unmatched: bool = False, recover_missing: bool = False,
) -> Plan:
    """Classify every row (see _classify) and build the plan. Reads only. `create_unmatched`: a filled card_id that is
    not a card of this board makes a new card. `recover_missing`: the recovery of the module notes (it only applies
    when the ledger refuses the workbook; otherwise the plan is refused as `recovery_not_applicable`)."""
    plans, cards, columns = _classify(db, board_id, read, people, create_unmatched)
    fp = fingerprint(company_slug, board_id, read)
    previous = ledger.find_by_fingerprint(db, board_id, fp)
    if previous is None:
        result = Plan(plans, cards, fp, _digest(fp, plans, cards, create_unmatched), columns, create_unmatched=create_unmatched)
        if recover_missing:
            result.recovery_refusal = ImportRefused(
                409, "recovery_not_applicable", "This workbook was not applied before on this board, so there is nothing to recover")
        return result
    # The ledger refuses an ordinary import of this workbook. Work out what a recovery would do, to offer it (or to apply it).
    recovery_rows, info = _recovery_rows(db, board_id, fp, read, people, columns_hint=None)
    if recover_missing:
        refusal = None if info["available"] else ImportRefused(409, "recovery_unavailable", info["message"])
        digest = _digest(fp, recovery_rows, {}, True, f"{info['high_water']}")
        guard = hashlib.sha256(f"{fp}:recovery:{info['high_water']}".encode()).hexdigest()
        return Plan(recovery_rows, {}, fp, digest, columns, previous=previous, create_unmatched=True, recovery=True,
                    repeat_guard=guard, recovery_info=info, recovery_refusal=refusal)
    result = Plan(plans, cards, fp, _digest(fp, plans, cards, create_unmatched), columns, previous=previous,
                  create_unmatched=create_unmatched, recovery_info=info)
    result.blocked_by = _already_imported(previous)
    return result


def _recovery_rows(db: Session, board_id: int, fp: str, read: CommercialReadResult, people: dict[str, int], columns_hint=None):
    """(rows, info) of a recovery of this workbook on this board. Rows: the eligible unmatched-id creates stay creates;
    every other valid row becomes a skipped row; invalid rows stay invalid (they block, as in any import)."""
    rows, _cards, _columns = _classify(db, board_id, read, people, True)
    entries = ledger.all_by_fingerprint(db, board_id, fp)
    on_board = {card_id for (card_id,) in db.query(Card.id).join(Column).filter(Column.board_id == board_id)}
    restored_present: set[int] = set()   # source ids whose restored card still exists here
    legacy_present = 0                   # cards created by entries that did not record source ids and still exist here
    for entry in entries:
        changes = json.loads(entry.changes_json or "{}")
        mapping = changes.get("created_from")
        if mapping is None:
            legacy_present += sum(1 for card_id in changes.get("created", []) if card_id in on_board)
        else:
            restored_present |= {int(src) for src, new in mapping.items() if new in on_board}
    out = []
    eligible = 0
    skipped = {"exists": 0, "empty_id": 0, "already_restored": 0}
    for r in rows:
        if r.action == INVALID:
            out.append(r)
        elif r.action == CREATE and r.unmatched_id and r.card_id not in restored_present:
            out.append(r)
            eligible += 1
        else:
            reason = ("already_restored" if r.action == CREATE and r.unmatched_id
                      else "empty_id" if r.action == CREATE else "exists")
            skipped[reason] += 1
            out.append(RowPlan(r.row_number, SKIPPED, card_id=r.card_id, title=r.title, stage=r.stage, skip_reason=reason))
    available, message = True, ""
    if legacy_present:
        available = False
        message = ("Cards created by an earlier import of this workbook still exist on this board, and that import did not "
                   "record which rows they came from, so restoring could duplicate them. Delete those cards first")
    elif eligible == 0 and not any(r.action == INVALID for r in out):  # invalid rows are reported as such, not as "nothing missing"
        available, message = False, "No card of this workbook is missing from this board"
    return out, {"available": available, "message": message, "eligible": eligible, "skipped": skipped,
                 "high_water": high_water(db)}


def _already_imported(entry) -> ImportRefused:
    return ImportRefused(
        409, "already_imported",
        "This workbook was already imported on this board and cannot be applied again, even if its cards were "
        "changed or deleted since. Nothing was changed. To import similar data, change a business value in the workbook",
        import_summary=ledger.summary(entry),
    )


def apply_plan(
    db: Session, board_id: int, plan: Plan, context, *, filename: str, file_sha256: str, recheck=None,
) -> dict:
    """Write the plan in the caller's open transaction (the caller holds the write lock and commits).

    Raises ImportRefused for a refusal; any other exception propagates and the caller rolls back. Nothing is
    committed here, so the card changes and the ledger row stand or fall together.
    """
    refusal = plan.refusal()
    if refusal:
        raise refusal
    if recheck:
        recheck()
    next_position: dict[int, int] = {}
    for column in plan.columns:
        last = db.query(Card.position).filter(Card.column_id == column.id).order_by(Card.position.desc()).first()
        next_position[column.id] = (last[0] + 1) if last else 0
    touched: set[int] = set()
    created: list[int] = []
    created_from: dict[str, int] = {}  # source card_id -> the new card's id, for rows made new because the id was not here
    updated: dict[int, dict] = {}
    admin = context.can_manage_cards

    for row in plan.rows:
        if row.action == CREATE:
            fields = dict(row.values)
            if fields.get("deal_value") is not None or fields.get("deal_value_next_year"):
                fields["currency"] = DEAL_CURRENCY
            fields.setdefault("deal_value_next_year", 0.0)
            card = Card(column_id=row.column_id, position=next_position[row.column_id],
                        **new_card_fields(context, fields))
            next_position[row.column_id] += 1
            touched.add(row.column_id)
            db.add(card)
            db.flush()
            audit_new_card_owner(db, board_id, card, context)
            created.append(card.id)
            if row.unmatched_id and row.card_id is not None:
                created_from[str(row.card_id)] = card.id
        elif row.action == UPDATE:
            card = plan.cards[row.card_id]
            changes = apply_card_updates(db, card, dict(row.values), context, admin=admin) if row.values else {}
            if "stage" in row.changed:
                touched.add(card.column_id)
                changes["stage"] = {"old": card.column.title, "new": row.stage}
                card.column_id = row.column_id
                card.position = next_position[row.column_id]
                next_position[row.column_id] += 1
                touched.add(row.column_id)
            updated[card.id] = changes
        db.flush()

    for column_id in touched:  # close the gaps moved cards left, keeping everyone's relative order
        members = sorted(db.query(Card).filter(Card.column_id == column_id).all(), key=lambda c: (c.position, c.id))
        for index, member in enumerate(members):
            if member.position != index:
                member.position = index
    db.flush()

    counts = {"created": len(created), "updated": len(updated), "unchanged": plan.count(UNCHANGED)}
    entry = ledger.ExcelImport(
        board_id=board_id, user_id=context.user_id, filename=filename[:255], file_sha256=file_sha256,
        fingerprint=plan.fingerprint, repeat_guard=plan.repeat_guard or plan.fingerprint,
        created_count=counts["created"], updated_count=counts["updated"], unchanged_count=counts["unchanged"],
        changes_json=json.dumps({"created": created, "created_from": created_from, "recovery": plan.recovery,
                                 "updated": {str(k): v for k, v in updated.items()}}, default=str),
        created_at=ledger.now(),
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        guard = ledger.find_guard(db, board_id, plan.repeat_guard or plan.fingerprint)
        raise (_already_imported(guard) if guard else ImportRefused(
            409, "import_conflict", "The board changed while the import was running; build the preview again"))
    if recheck:
        recheck()
    return {"import_id": entry.id, **counts, "applied_at": entry.created_at.isoformat()}


def plan_body(plan: Plan, read: CommercialReadResult) -> dict:
    """The preview payload: counts and one entry per row. An update lists `diffs` (field, current value, proposed value;
    None = empty or unassigned) for every field it changes; those are the card's own values on this board."""
    refusal = plan.refusal()
    return {
        "filename": read.file.filename,
        "file_sha256": read.file.sha256,
        "summary": {
            "rows": len(plan.rows), "creates": plan.count(CREATE), "updates": plan.count(UPDATE),
            "unchanged": plan.count(UNCHANGED), "invalid": plan.count(INVALID),
            "warnings": sum(len(r.warnings) for r in plan.rows) + len(read.file_warnings),
            **({"skipped": plan.count(SKIPPED)} if plan.recovery else {}),  # only a recovery skips rows
        },
        "rows": [
            {"row": r.row_number, "action": r.action, "card_id": r.card_id, "title": r.title, "stage": r.stage,
             "changed": r.changed, "diffs": r.diffs, "errors": r.errors, "warnings": r.warnings,
             "unmatched_id": r.unmatched_id, "skip_reason": r.skip_reason}
            for r in plan.rows
        ],
        "file_warnings": [issue_dict(w) for w in read.file_warnings],
        # How many of the creates are rows whose card_id is not a card of this board (only with the opt-in).
        "creates_from_unmatched_ids": sum(1 for r in plan.rows if r.action == CREATE and r.unmatched_id),
        # Recovery of cards missing from this board (only meaningful when the ledger refuses the ordinary import).
        "recovery": {
            "mode": plan.recovery,
            "available": bool(plan.recovery_info and plan.recovery_info["available"]),
            "eligible": plan.recovery_info["eligible"] if plan.recovery_info else 0,
            "skipped": plan.recovery_info["skipped"] if plan.recovery_info else None,
            "message": plan.recovery_info["message"] if plan.recovery_info else None,
        },
        "confirmable": refusal is None,
        "blocked_reason": refusal.code if refusal else None,
        "already_imported": refusal.extra.get("import_summary") if refusal and refusal.code == "already_imported" else None,
        "previous_import": ledger.summary(plan.previous) if plan.previous is not None else None,
        "plan_digest": plan.digest,
    }
