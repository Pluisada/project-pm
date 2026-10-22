"""Salcomp Bid import endpoints (Part 22E): preview, confirm, batch history.

Admin-only, all of them. The routes only parse the upload, check that a
confirmation matches the preview the admin saw, and translate errors to HTTP:
reading is bid_excel_reader.py, the rules are bid_import_preview.py and every
write (and its transaction) is bid_import_service.py.

Upload: multipart/form-data with one `file` part (the .xlsx) and a `decisions`
part (a JSON array of ImportDecision). The form is parsed only after the admin
check and a Content-Length check, so a member or an anonymous client can't make
the server read an upload at all. The bytes stay in memory (Starlette spools a
part over 1 MiB to an anonymous temporary file, closed explicitly by _read_form on
every path, errors and oversize cut-offs included);
nothing of the file is kept except its name and SHA-256 in import_batches.

Confirmation: the preview returns a `preview_token`, signed with SECRET_KEY
(auth.create_purpose_token, so it can never act as a login token), naming the
admin, the board, the file's SHA-256, the decisions, the board's latest
completed import and a fingerprint of the preview itself, valid for
PREVIEW_TOKEN_MINUTES. Confirming re-sends the file and the decisions; the
route re-reads the file, checks every claim, rebuilds the preview from the
current database and refuses unless it is exactly what was shown. The service
then rebuilds it once more under the write lock before writing anything.
"""
import json
import logging
from datetime import timedelta
from hashlib import sha256
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

import card_reviews
from auth import create_purpose_token, decode_purpose_token
from bid_excel_reader import DEFAULT_LIMITS, BidReadResult, read_bid_excel
from bid_import_preview import ImportPreview, RowDecision
from bid_import_revert import (
    READY, RevertBusy, RevertNotAuthorized, RevertOutcomeUnconfirmed, RevertPlan, RevertRefused, execute_revert,
    revert_preview,
)
from bid_import_service import ImportConflict, ImportNotAuthorized, ImportRejected, apply_bid_import, preview_bid_import
from database import get_db
from deps import require_admin
from rate_limit import limit_imports
from models import Board, CompanyUser, ImportBatch, User
from schemas import (
    ImportBatchDetail, ImportBatchList, ImportBatchResponse, ImportDecision, ImportResult, RevertPlanResponse,
    RevertRequest, RevertResult,
)

router = APIRouter(prefix="/api", tags=["imports"])
logger = logging.getLogger(__name__)

PREVIEW_TOKEN_PURPOSE = "bid_import_confirm"
PREVIEW_TOKEN_MINUTES = 30
LIMITS = DEFAULT_LIMITS
# Allowance for the multipart envelope and the decisions part, on top of the file.
FORM_OVERHEAD_BYTES = 1024 * 1024
# Stored (card_bid_details.cpm) but never shown in a response: it's a person's name.
PREVIEW_HIDDEN_FIELDS = ("cpm",)
# Longest text of an imported/current value in a revert conflict.
REVERT_VALUE_MAX_CHARS = 200
REVERT_RETRY_AFTER_SECONDS = "5"

_decisions_adapter = TypeAdapter(list[ImportDecision])


# --- request parsing --------------------------------------------------------------

def _board_or_404(db: Session, board_id: int) -> Board:
    board = db.get(Board, board_id)
    if board is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Board not found")
    return board


class _UploadTooLarge(Exception):
    pass


async def _parse_form(request: Request) -> FormData:
    """The request's form. Starlette closes the temporary files of a part only
    when the multipart parser itself rejects the body; any other error raised
    while the body is read (the size counter, a dropped connection) would leave
    them open until garbage collection, so the parser is driven here and its
    files are closed on every failure."""
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        return await request.form(max_files=1, max_fields=3)  # no file parts
    parser = MultiPartParser(request.headers, request.stream(), max_files=1, max_fields=3)
    try:
        return await parser.parse()
    except BaseException as exc:
        # _files_to_close_on_error is private to Starlette (0.41); its own
        # error path uses it the same way. Closing twice is harmless.
        for file in parser._files_to_close_on_error:
            file.close()
        if isinstance(exc, MultiPartException):
            raise HTTPException(status_code=400, detail=exc.message) from None
        raise


async def _read_form(request: Request) -> tuple[bytes, str, list[ImportDecision], dict]:
    """(file bytes, file name, decisions, other text fields) of the upload."""
    length = request.headers.get("content-length")
    if length is None or not length.isdigit():
        raise HTTPException(status_code=status.HTTP_411_LENGTH_REQUIRED, detail="Content-Length required")
    if int(length) > LIMITS.max_file_bytes + FORM_OVERHEAD_BYTES:
        raise _file_too_large()
    # Content-Length is only a claim: count the bytes actually received so a
    # lying client (or a server that doesn't enforce the framing) can't stream
    # an unbounded body into Starlette's temporary files.
    limit = LIMITS.max_file_bytes + FORM_OVERHEAD_BYTES
    received = 0

    async def counted_receive():
        nonlocal received
        message = await request.receive()
        if message["type"] == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise _UploadTooLarge
        return message

    try:
        form = await _parse_form(Request(request.scope, counted_receive))
    except _UploadTooLarge:
        raise _file_too_large() from None
    # Closed explicitly on every path out (also the errors below), so the
    # temporary files never wait for the garbage collector.
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise HTTPException(status_code=422, detail="Upload the .xlsx as the 'file' field")
        # One byte over the reader's limit is enough for it to refuse the file.
        data = await upload.read(LIMITS.max_file_bytes + 1)
        filename = upload.filename
        fields = {key: value for key, value in form.items() if isinstance(value, str)}
    finally:
        await form.close()
    try:
        decisions = _decisions_adapter.validate_json(fields.get("decisions") or "[]")
    except ValidationError:
        raise HTTPException(
            status_code=422,
            detail="'decisions' must be a JSON array of {row_number, target_stage, confirm_proposed_stage, "
                   "skip, confirm_update, confirm_move}",
        ) from None
    if len(decisions) > LIMITS.max_rows:
        raise HTTPException(status_code=422, detail="Too many decisions")
    return data, filename, decisions, fields


def _file_too_large() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        detail=f"The file is larger than {LIMITS.max_file_bytes} bytes",
    )


def _read_file(data: bytes, filename: str) -> BidReadResult:
    """Read the workbook; a file that can't be interpreted at all is refused here."""
    result = read_bid_excel(data, filename, LIMITS)
    if not result.ok:
        if any(issue.code == "file_too_large" for issue in result.file_errors):
            raise _file_too_large()
        raise HTTPException(
            status_code=422,
            detail={
                "message": "The file can't be imported",
                "file_errors": [_issue(issue) for issue in result.file_errors],
            },
        )
    return result


def _row_decisions(decisions: list[ImportDecision]) -> tuple[RowDecision, ...]:
    return tuple(sorted((RowDecision(**d.model_dump()) for d in decisions), key=lambda d: d.row_number))


# --- serialization ------------------------------------------------------------------

def _issue(issue) -> dict:
    return {
        "severity": issue.severity.value, "code": issue.code, "message": issue.message,
        "row": issue.row, "column": issue.column, "field": issue.field,
    }


def _stage(stage) -> Optional[dict]:
    return None if stage is None else {"column_id": stage.column_id, "title": stage.title, "stage_kind": stage.stage_kind}


def _row(row) -> dict:
    fields = row.fields
    return {
        "row_number": row.row_number,
        "code": row.code,
        "customer": row.customer,
        "project_name": row.project_name,
        "source_status": row.source_status,
        "source_stage": row.source_stage,
        "source_key": row.source_key,
        "action": row.action.value,
        "classification": {
            "status": row.classification.status.value,
            "target": _stage(row.classification.target),
            "proposed": _stage(row.classification.proposed),
        },
        "target_stage": row.target_stage,
        "reasons": [_issue(i) for i in row.reasons],
        "errors": [_issue(i) for i in row.errors],
        "warnings": [_issue(i) for i in row.warnings],
        "required_decisions": [d.value for d in row.required_decisions],
        "fields": None if fields is None else {
            "card": fields.card,
            "bid_details": {k: v for k, v in fields.bid_details.items() if k not in PREVIEW_HIDDEN_FIELDS},
            "source_ref": fields.source_ref,
            "not_mapped": fields.not_mapped,
        },
        "duplicate_rows": list(row.duplicate_rows),
        "existing_card_id": row.existing_card_id,
        "changed_fields": [
            {"field": c.field, "current": c.current, "incoming": c.incoming} for c in row.changed_fields
            if c.field.removeprefix("bid_details.") not in PREVIEW_HIDDEN_FIELDS
        ],
        "stage_change": None if row.stage_change is None else {"from": row.stage_change[0], "to": row.stage_change[1]},
        "move_confirmed": row.move_confirmed,
    }


def _preview_body(read_result: BidReadResult, preview: ImportPreview) -> dict:
    sheet = read_result.sheet
    return jsonable_encoder({
        "file": {
            "filename": preview.file.filename,
            "size_bytes": preview.file.size_bytes,
            "sha256": preview.file.sha256,
        },
        "sheet": None if sheet is None else {
            "name": sheet.name,
            "missing_optional_columns": list(sheet.missing_optional_columns),
            "ignored_columns": list(sheet.ignored_columns),
        },
        "file_warnings": [_issue(i) for i in read_result.file_warnings],
        "read_summary": vars(read_result.summary),
        "summary": vars(preview.summary),
        "rows": [_row(row) for row in preview.rows],
        "written": preview.written,
        "notice": preview.notice,
    })


def _fingerprint(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _preview_fingerprint(body: dict) -> str:
    """Everything the admin was shown except the file's name (the SHA-256 is
    checked on its own; the same bytes under another name are the same file)."""
    return _fingerprint({**body, "file": {**body["file"], "filename": None}})


def _decisions_fingerprint(decisions: tuple[RowDecision, ...]) -> str:
    return _fingerprint([vars(d) for d in decisions])


def _last_completed_batch_id(db: Session, board_id: int) -> int:
    return db.query(func.max(ImportBatch.id)).filter(
        ImportBatch.board_id == board_id, ImportBatch.status == "completed",
    ).scalar() or 0


def _revert_state(batch: ImportBatch) -> dict:
    ledger = batch.ledger
    if ledger is None:
        reason = "batch_not_completed" if batch.status != "completed" else "no_ledger"
        return {"has_ledger": False, "revertible": False, "reason": reason, "reverted": False}
    if ledger.reverted_at is not None:
        reverter = Session.object_session(batch).get(CompanyUser, ledger.reverted_by_user_id) if ledger.reverted_by_user_id else None
        return {
            "has_ledger": True, "revertible": False, "reason": "already_reverted", "reverted": True,
            "reverted_at": ledger.reverted_at,
            "reverted_by_user_id": ledger.reverted_by_user_id,
            "reverted_by_username": reverter.username if reverter else None,
            "outcome": ledger.revert_outcome,
            "no_commercial_changes": ledger.no_commercial_changes,
            "cards_deleted": ledger.cards_deleted_count,
            "cards_restored": ledger.cards_restored_count,
            "moves_reverted": ledger.moves_reverted_count,
            "already_deleted": ledger.already_deleted_count,
            "summary": json.loads(ledger.result_summary_json) if ledger.result_summary_json else None,
        }
    revertible = batch.status == "completed"
    return {
        "has_ledger": True, "revertible": revertible, "reverted": False,
        "reason": None if revertible else "batch_not_completed",
    }


def _batch(batch: ImportBatch) -> dict:
    return {
        **{name: getattr(batch, name) for name in ImportBatchResponse.model_fields if name not in ("username", "revert")},
        "username": batch.user.username if batch.user else None,
        "revert": _revert_state(batch),
    }


# --- endpoints ---------------------------------------------------------------------

def _build_preview(db, read_result, board_id, decisions) -> ImportPreview:
    try:
        return preview_bid_import(db, read_result, board_id, decisions)
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ImportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


async def preview_import_impl(db: Session, board_id: int, request: Request, user_id: int, scope: Optional[str] = None):
    """Read the uploaded Bids report and propose an action per row. Writes nothing.

    `scope` is None for the legacy single database; in a company it is the company slug, signed into the token so a
    preview made in one company can never confirm in another."""
    _board_or_404(db, board_id)
    data, filename, decisions, _ = await _read_form(request)
    read_result = _read_file(data, filename)
    decisions = _row_decisions(decisions)
    preview = _build_preview(db, read_result, board_id, decisions)
    body = _preview_body(read_result, preview)
    # A decision that doesn't fit its row makes the service refuse the whole
    # file, so such a preview can't be confirmed: fix the decisions first.
    confirmable = not any(
        reason["code"] == "decision_not_applicable" for row in body["rows"] for reason in row["reasons"]
    )
    token = None
    if confirmable:
        token = create_purpose_token(
            str(user_id), PREVIEW_TOKEN_PURPOSE, timedelta(minutes=PREVIEW_TOKEN_MINUTES),
            board_id=board_id,
            file_sha256=read_result.file.sha256,
            decisions_sha256=_decisions_fingerprint(decisions),
            preview_sha256=_preview_fingerprint(body),
            last_batch_id=_last_completed_batch_id(db, board_id),
            **({"company": scope} if scope else {}),
        )
    return {**body, "confirmable": confirmable, "preview_token": token, "expires_in_minutes": PREVIEW_TOKEN_MINUTES}


@router.post("/boards/{board_id}/imports/preview")
async def preview_import(
    board_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    _: None = Depends(limit_imports),
    db: Session = Depends(get_db),
):
    return await preview_import_impl(db, board_id, request, current_user.id)


async def confirm_import_impl(
    db: Session, board_id: int, request: Request, current_user, scope: Optional[str] = None, recheck=None
):
    """Apply a previewed import: the same file, the same decisions, the
    preview_token from the preview and confirm=true. `current_user` needs only `.id`.

    `recheck` (company routes) re-reads the caller's authority and raises HTTPException when it is gone; it runs just
    before the write starts (after the upload and the preview were processed) and again, as ImportNotAuthorized,
    just before the commit."""
    _board_or_404(db, board_id)
    data, filename, decisions, fields = await _read_form(request)
    if fields.get("confirm") != "true":
        raise HTTPException(status_code=422, detail="Set confirm=true to apply the import")
    claims = decode_purpose_token(fields.get("preview_token") or "", PREVIEW_TOKEN_PURPOSE)
    if (claims is None or claims.get("sub") != str(current_user.id) or claims.get("board_id") != board_id
            or claims.get("company") != scope):
        raise HTTPException(status_code=422, detail="Invalid or expired preview; build the preview again")
    read_result = _read_file(data, filename)
    if read_result.file.sha256 != claims.get("file_sha256"):
        raise HTTPException(status_code=422, detail="This is not the file that was previewed")
    decisions = _row_decisions(decisions)
    if _decisions_fingerprint(decisions) != claims.get("decisions_sha256"):
        raise HTTPException(status_code=422, detail="The decisions differ from the previewed ones")
    if _last_completed_batch_id(db, board_id) != claims.get("last_batch_id"):
        raise HTTPException(
            status_code=409,
            detail="Another import was completed on this board since the preview (or this one was already "
                   "applied); build the preview again",
        )
    preview = _build_preview(db, read_result, board_id, decisions)
    if _preview_fingerprint(_preview_body(read_result, preview)) != claims.get("preview_sha256"):
        raise HTTPException(status_code=409, detail="The data changed since the preview; build it again")

    def before_commit():
        try:
            recheck()
        except HTTPException:
            raise ImportNotAuthorized("Not authorized") from None

    if recheck is not None:
        recheck()
    try:
        extra = {"before_commit": before_commit} if recheck is not None else {}  # legacy callers: unchanged call
        outcome = apply_bid_import(db, read_result, preview, decisions, board_id, current_user, **extra)
    except ImportNotAuthorized:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None
    except ImportRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except ImportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except card_reviews.ReviewsNotInitialized as exc:
        # The version schema broke after the route's gate (central mode only; legacy never raises it). The service has
        # already rolled back and recorded the failed attempt; only the answer differs from the generic 500.
        raise _error(exc.status, exc.code, exc.message) from None
    except Exception as exc:
        # Rolled back and recorded as a failed batch by the service. Only the
        # type is logged: the message may quote SQL parameters (row content).
        logger.error("Bid import failed unexpectedly (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="The import failed; nothing was written") from None

    return {
        "batch": _batch(db.get(ImportBatch, outcome.batch_id)) if outcome.batch_id is not None else None,
        "no_op": outcome.no_op,
        "created_card_ids": list(outcome.created_card_ids),
        "updated_card_ids": list(outcome.updated_card_ids),
        "moved_card_ids": list(outcome.moved_card_ids),
        "skipped": outcome.skipped,
        "review": outcome.review,
    }


@router.post("/boards/{board_id}/imports", response_model=ImportResult, status_code=201)
async def confirm_import(
    board_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    _: None = Depends(limit_imports),
    db: Session = Depends(get_db),
):
    return await confirm_import_impl(db, board_id, request, current_user)


def list_imports_impl(db: Session, board_id: int, limit: int, offset: int):
    """This board's import attempts (completed and failed), newest first."""
    _board_or_404(db, board_id)
    query = db.query(ImportBatch).filter(ImportBatch.board_id == board_id)
    batches = (
        query.options(selectinload(ImportBatch.ledger)).order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .offset(offset).limit(limit).all()
    )
    return {"items": [_batch(b) for b in batches], "total": query.count(), "limit": limit, "offset": offset}


@router.get("/boards/{board_id}/imports", response_model=ImportBatchList)
async def list_imports(
    board_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return list_imports_impl(db, board_id, limit, offset)


def get_import_impl(db: Session, batch_id: int):
    """One import attempt: metadata, state, counts and persisted diagnostics."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    return {
        **_batch(batch),
        "mapping": json.loads(batch.mapping_json) if batch.mapping_json else None,
        "card_changes_count": len(batch.card_changes),
    }


@router.get("/imports/{batch_id}", response_model=ImportBatchDetail)
async def get_import(
    batch_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return get_import_impl(db, batch_id)


# --- revert (Part 23) -----------------------------------------------------------------------------

def _error(status_code: int, code: str, message: str, headers: Optional[dict] = None, **extra) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra}, headers=headers)


def _cut(value):
    return value[:REVERT_VALUE_MAX_CHARS] if isinstance(value, str) else value


def _blocker(blocker) -> dict:
    return {
        "code": blocker.code, "card_id": blocker.card_id, "field": blocker.field,
        "imported": _cut(blocker.imported), "current": _cut(blocker.current), "batch_ids": list(blocker.batch_ids),
    }


def _operation(op) -> dict:
    return {
        "kind": op.kind, "card_id": op.card_id,
        "fields": sorted(op.data["fields"]) if "fields" in op.data else None,
        "from_column_id": op.data.get("from_column_id"), "to_column_id": op.data.get("to_column_id"),
    }


def _plan_body(plan: RevertPlan) -> dict:
    return jsonable_encoder({
        "batch_id": plan.batch_id,
        "status": plan.status,
        "reason": plan.reason,
        "revertible": plan.status == READY,
        "no_commercial_changes": plan.no_commercial_changes,
        "ledger_version": plan.ledger_version,
        "plan_fingerprint": plan.plan_fingerprint,
        "counts": plan.counts,
        "blockers": [_blocker(b) for b in plan.blockers],
        "dependent_batches": list(plan.dependent_batches),
        "items": [
            {
                "item_id": i.item_id, "card_id": i.card_id, "source_key": i.source_key, "action": i.action,
                "observation": i.observation, "classification": i.classification,
                "operations": [_operation(op) for op in i.operations],
                "blockers": [_blocker(b) for b in i.blockers],
            }
            for i in plan.items
        ],
    })


def _busy(exc: RevertBusy) -> HTTPException:
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE, "import_busy", "The database is busy; nothing was reverted. Try again shortly.",
        headers={"Retry-After": REVERT_RETRY_AFTER_SECONDS}, phase=exc.phase, applied=False,
    )


def _not_found() -> HTTPException:
    return _error(status.HTTP_404_NOT_FOUND, "batch_not_found", "Import not found")


_REFUSALS = {
    "not_revertible": "This import can't be reverted",
    "already_reverted": "This import was already reverted",
    "plan_changed": "The data changed since the revert preview; review it again",
    "revert_blocked": "The revert is blocked by conflicts; nothing was changed",
}


def revert_preview_impl(db: Session, batch_id: int):
    """What reverting this batch would do, with the plan_fingerprint to confirm it. Writes nothing."""
    try:
        plan = revert_preview(db, batch_id)
    except RevertRefused:
        raise _not_found() from None
    except RevertBusy as exc:
        raise _busy(exc) from None
    except Exception as exc:
        logger.error("Bid import revert preview failed unexpectedly (%s)", type(exc).__name__)
        raise _error(500, "revert_failed", "The revert preview failed") from None
    return _plan_body(plan)


@router.get("/imports/{batch_id}/revert-preview", response_model=RevertPlanResponse)
async def get_revert_preview(
    batch_id: int,
    current_user: User = Depends(require_admin),
    _: None = Depends(limit_imports),
    db: Session = Depends(get_db),
):
    return revert_preview_impl(db, batch_id)


def revert_import_impl(db: Session, batch_id: int, current_user, plan_fingerprint: str, recheck=None):
    """Revert the batch in one transaction, if the plan is still the one the admin previewed."""
    def before_commit():
        try:
            recheck()
        except HTTPException:
            raise RevertNotAuthorized() from None

    if recheck is not None:
        recheck()
    try:
        extra = {"before_commit": before_commit} if recheck is not None else {}  # legacy callers: unchanged call
        outcome = execute_revert(db, batch_id, current_user, plan_fingerprint, **extra)
    except RevertNotAuthorized:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None
    except RevertRefused as exc:
        if exc.code == "batch_not_found":
            raise _not_found() from None
        if exc.code not in _REFUSALS:
            raise _error(500, "revert_failed", "The revert failed; nothing was changed") from None
        extra = {}
        if exc.code == "revert_blocked" and exc.plan is not None:
            extra = {"blockers": _plan_body(exc.plan)["blockers"], "dependent_batches": list(exc.plan.dependent_batches)}
        raise _error(status.HTTP_409_CONFLICT, exc.code, _REFUSALS[exc.code], **extra) from None
    except RevertBusy as exc:
        raise _busy(exc) from None
    except card_reviews.ReviewsNotInitialized as exc:
        # Rolled back by the executor before it re-raised (central mode only).
        raise _error(exc.status, exc.code, exc.message) from None
    except RevertOutcomeUnconfirmed:
        raise _error(
            500, "revert_outcome_unconfirmed",
            "The result of the revert could not be confirmed; check the import history before trying again",
        ) from None
    except Exception as exc:
        # Rolled back by the executor; only the type is logged (messages may quote SQL parameters).
        logger.error("Bid import revert failed unexpectedly (%s)", type(exc).__name__)
        raise _error(500, "revert_failed", "The revert failed; nothing was changed") from None

    return {
        "batch": _batch(db.get(ImportBatch, batch_id)),
        "outcome": outcome.outcome,
        "no_commercial_changes": outcome.no_commercial_changes,
        "counts": {
            "cards_deleted": outcome.cards_deleted, "cards_restored": outcome.cards_restored,
            "moves_reverted": outcome.moves_reverted, "already_deleted": outcome.already_deleted,
            "bid_details_deleted": outcome.bid_details_deleted, "already_reverted": outcome.already_reverted,
            "observed": outcome.observed,
        },
    }


@router.post("/imports/{batch_id}/revert", response_model=RevertResult)
async def revert_import(
    batch_id: int,
    body: RevertRequest,
    current_user: User = Depends(require_admin),
    _: None = Depends(limit_imports),
    db: Session = Depends(get_db),
):
    return revert_import_impl(db, batch_id, current_user, body.plan_fingerprint)
