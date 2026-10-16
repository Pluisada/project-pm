"""Part 22E: the Bid import endpoints, over a temp SQLite file with the full
schema (never backend/pm.db). Synthetic workbooks (fictitious data) only."""
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import bid_import_service
import import_routes
from auth import create_purpose_token, create_user_access_token
from database import get_db
from lead_options import PIPELINE_STAGES
from main import app
from models import Base, Board, BoardColumn, Card, CardStageEvent, User
from test_bid_excel_reader import build, row as excel_row
from auth_test_utils import VERIFIED

TABLES = ("boards", "columns", "cards", "card_bid_details", "card_source_refs", "card_stage_events",
          "card_import_changes", "card_edit_history", "import_batches", "undo_entries", "users")
COMMERCIAL = tuple(t for t in TABLES if t != "import_batches")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ROWS = (
    excel_row(**{"Código": "000101-01"}),                                                         # row 2: -> Proposal
    excel_row(**{"Código": "000102-01", "CAT": "WON", "Estágio": "AWARD GIVEN"}),                 # row 3: -> Award
    excel_row(**{"Código": "000103-01", "CAT": "CLOSED", "Estágio": "NOT AWARDED BY CUSTOMER"}),  # row 4: confirm -> Lost
    excel_row(**{"Código": "000104-01", "CAT": "CLOSED", "Estágio": "CLOSED (BY SALCOMP)"}),      # row 5: review
)
DECISIONS = [{"row_number": 4, "confirm_proposed_stage": True}]


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "imports.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as db:
        admin = User(email_verified_at=VERIFIED, username="admin", password_hash="x", role="admin", email="admin@example.test")
        other_admin = User(email_verified_at=VERIFIED, username="admin2", password_hash="x", role="admin")
        member = User(email_verified_at=VERIFIED, username="member", password_hash="x", role="member", full_name="Member Person")
        db.add_all([admin, other_admin, member])
        db.flush()
        boards = []
        for title in ("Pipeline", "Other"):
            board = Board(title=title, user_id=admin.id)
            for i, (stage, kind) in enumerate(PIPELINE_STAGES):
                BoardColumn(board=board, title=stage, position=i, stage_kind=kind)
            boards.append(board)
        db.add_all(boards)
        db.commit()
        board_id, other_board_id = boards[0].id, boards[1].id

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    class Env:
        pass

    e = Env()
    e.path, e.Session, e.board, e.other_board = path, Session, board_id, other_board_id
    e.client = TestClient(app, raise_server_exceptions=False)
    with Session() as db:
        e.tokens = {
            u.username: create_user_access_token(u)
            for u in db.query(User).filter(User.username.in_(("admin", "admin2", "member")))
        }
    yield e
    app.dependency_overrides.clear()
    engine.dispose()


def auth(env, user="admin"):
    return {"Authorization": f"Bearer {env.tokens[user]}"}


_built = {}


def xlsx(*rows):
    """The workbook for these rows, built once: openpyxl stamps the current time
    into every workbook, so rebuilding "the same" file can give other bytes."""
    rows = list(rows or ROWS)
    key = json.dumps(rows, default=str, sort_keys=True)
    if key not in _built:
        _built[key] = build(rows)
    return _built[key]


def preview(env, data=None, decisions=(), board=None, user="admin", filename="relatorio-leads.xlsx"):
    return env.client.post(
        f"/api/boards/{board or env.board}/imports/preview",
        files={"file": (filename, xlsx() if data is None else data, XLSX)},
        data={"decisions": json.dumps(list(decisions))},
        headers=auth(env, user),
    )


def confirm(env, token, data=None, decisions=(), board=None, user="admin", confirm_flag="true"):
    fields = {"decisions": json.dumps(list(decisions)), "confirm": confirm_flag}
    if token is not None:
        fields["preview_token"] = token
    return env.client.post(
        f"/api/boards/{board or env.board}/imports",
        files={"file": ("relatorio-leads.xlsx", xlsx() if data is None else data, XLSX)},
        data=fields,
        headers=auth(env, user),
    )


def previewed(env, data=None, decisions=(), **kw):
    response = preview(env, data, decisions, **kw)
    assert response.status_code == 200, response.text
    return response.json()


def run(env, data=None, decisions=()):
    body = previewed(env, data, decisions)
    response = confirm(env, body["preview_token"], data, decisions)
    assert response.status_code == 201, response.text
    return response.json()


def snapshot(path, tables=TABLES):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall() for t in tables}
    finally:
        conn.close()


def batches(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT status, failure_kind, failure_reason FROM import_batches ORDER BY id").fetchall()
    finally:
        conn.close()


# --- authentication and authorization -------------------------------------------------

def endpoints(env):
    return (
        ("post", f"/api/boards/{env.board}/imports/preview"),
        ("post", f"/api/boards/{env.board}/imports"),
        ("get", f"/api/boards/{env.board}/imports"),
        ("get", "/api/imports/1"),
    )


def test_unauthenticated_requests_are_refused(env):
    for method, url in endpoints(env):
        response = getattr(env.client, method)(url)
        assert response.status_code == 401, url
        response = getattr(env.client, method)(url, headers={"Authorization": "Bearer not-a-token"})
        assert response.status_code == 401, url


def test_members_are_refused_before_the_upload_is_read(env, monkeypatch):
    run(env, decisions=DECISIONS)  # batch 1 exists, so /imports/1 isn't refused for being missing
    before = snapshot(env.path)
    monkeypatch.setattr(import_routes, "_read_form", lambda request: pytest.fail("upload parsed for a member"))
    for method, url in endpoints(env):
        kwargs = {"files": {"file": ("relatorio-leads.xlsx", xlsx(), XLSX)}} if method == "post" else {}
        response = getattr(env.client, method)(url, headers=auth(env, "member"), **kwargs)
        assert response.status_code == 403, url
        assert response.json() == {"detail": "Admin access required"}
    assert snapshot(env.path) == before


def test_a_preview_token_is_not_a_login_token(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    response = env.client.get(f"/api/boards/{env.board}/imports", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_missing_board(env):
    assert preview(env, board=9999).status_code == 404
    assert confirm(env, "x", board=9999).status_code == 404
    assert env.client.get("/api/boards/9999/imports", headers=auth(env)).status_code == 404
    assert env.client.get("/api/imports/9999", headers=auth(env)).status_code == 404


# --- preview -------------------------------------------------------------------------------

def test_preview_classifies_rows_and_writes_nothing(env):
    before = snapshot(env.path)
    body = previewed(env, decisions=DECISIONS)
    assert snapshot(env.path) == before  # not even an import_batches row
    assert body["written"] is False and body["confirmable"] is True and body["preview_token"]
    assert body["file"]["filename"] == "relatorio-leads.xlsx" and len(body["file"]["sha256"]) == 64
    assert body["summary"]["total_rows"] == 4
    assert body["summary"]["by_action"] == {"create": 3, "review": 1}
    actions = {row["row_number"]: (row["action"], row["target_stage"], row["required_decisions"]) for row in body["rows"]}
    assert actions == {
        2: ("create", "Proposal", []),
        3: ("create", "Award", []),
        4: ("create", "Lost", []),
        5: ("review", None, ["choose_stage"]),
    }
    row2 = body["rows"][0]
    assert row2["fields"]["card"]["title"] == "Projeto Teste"
    assert row2["fields"]["bid_details"]["revenue_current_year_cents"] == 123456
    assert row2["fields"]["source_ref"]["source_updated_at"] == "2026-03-05"


def test_preview_lists_pending_decisions(env):
    body = previewed(env)
    pending = {row["row_number"]: row["required_decisions"] for row in body["rows"] if row["required_decisions"]}
    assert pending == {4: ["confirm_stage"], 5: ["choose_stage"]}
    assert body["rows"][2]["classification"]["proposed"]["title"] == "Lost"


def test_preview_hides_the_never_imported_cpm_name(env):
    response = preview(env)
    assert "Pessoa A" not in response.text  # CPM: a person's name, never imported
    assert "cpm" not in response.json()["rows"][0]["fields"]["not_mapped"]


@pytest.mark.parametrize("filename,data,code", [
    ("leads.csv", b"a,b\n1,2\n", "invalid_extension"),
    ("leads.xlsm", b"PK\x03\x04", "format_not_allowed"),
    ("leads.xls", b"\xd0\xcf\x11\xe0", "format_not_allowed"),
    ("leads.xlsx", b"not a workbook, whatever the content type says", "not_xlsx"),
    ("leads.xlsx", b"", "empty_file"),
])
def test_files_that_cannot_be_read_are_refused(env, filename, data, code):
    before = snapshot(env.path)
    response = preview(env, data=data, filename=filename)
    assert response.status_code == 422
    assert [e["code"] for e in response.json()["detail"]["file_errors"]] == [code]
    assert snapshot(env.path) == before


def test_a_request_over_the_size_limit_is_refused_before_parsing(env, monkeypatch):
    monkeypatch.setattr(import_routes, "LIMITS", replace(import_routes.LIMITS, max_file_bytes=1000))
    monkeypatch.setattr(import_routes, "FORM_OVERHEAD_BYTES", 100)
    response = preview(env, data=b"PK" + b"\0" * 5000)
    assert response.status_code == 413


def _multipart(payload: bytes) -> tuple[bytes, str]:
    boundary = "testboundary"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="x.xlsx"\r\n'
        f"Content-Type: {XLSX}\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def test_no_form_is_parsed_before_authentication_and_authorization(env, monkeypatch):
    from starlette.requests import Request

    def fail(self, **kwargs):
        pytest.fail("multipart form parsed before the admin check")

    monkeypatch.setattr(Request, "form", fail)
    body, content_type = _multipart(b"PK" + b"\0" * 100)
    for headers in ({}, auth(env, "member")):
        response = env.client.post(
            f"/api/boards/{env.board}/imports/preview", content=body,
            headers={**headers, "Content-Type": content_type},
        )
        assert response.status_code in (401, 403)


def test_a_body_without_content_length_is_refused(env):
    def chunks():
        yield b"x" * 10

    response = env.client.post(
        f"/api/boards/{env.board}/imports/preview", content=chunks(),
        headers={**auth(env), "Content-Type": "multipart/form-data; boundary=testboundary"},
    )
    assert response.status_code == 411


def test_a_body_larger_than_its_declared_content_length_is_cut_off(env, monkeypatch):
    monkeypatch.setattr(import_routes, "LIMITS", replace(import_routes.LIMITS, max_file_bytes=1000))
    monkeypatch.setattr(import_routes, "FORM_OVERHEAD_BYTES", 1000)
    body, content_type = _multipart(b"PK" + b"\0" * 100_000)
    response = env.client.post(
        f"/api/boards/{env.board}/imports/preview", content=body,
        headers={**auth(env), "Content-Type": content_type, "Content-Length": "500"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "The file is larger than 1000 bytes"}


def test_a_file_over_the_reader_limit_is_refused(env, monkeypatch):
    data = xlsx()
    monkeypatch.setattr(import_routes, "LIMITS", replace(import_routes.LIMITS, max_file_bytes=len(data) - 1))
    response = preview(env, data=data)
    assert response.status_code == 413
    assert "Traceback" not in response.text


def test_missing_file_part(env):
    response = env.client.post(
        f"/api/boards/{env.board}/imports/preview", data={"decisions": "[]"}, headers=auth(env),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("decisions", [
    "not json",
    json.dumps({"row_number": 2}),
    json.dumps([{"row_number": 2, "card_id": 1}]),            # no card ids from the client
    json.dumps([{"row_number": 2, "source_key": "v1:x"}]),    # nor source keys
    json.dumps([{"row_number": "2", "skip": True}]),
    json.dumps([{"row_number": 2, "skip": "yes"}]),
    json.dumps([{"row_number": 1, "skip": True}]),            # the header row
])
def test_malformed_decisions(env, decisions):
    response = env.client.post(
        f"/api/boards/{env.board}/imports/preview",
        files={"file": ("relatorio-leads.xlsx", xlsx(), XLSX)},
        data={"decisions": decisions},
        headers=auth(env),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("decisions,message", [
    ([{"row_number": 99, "skip": True}], "not in the file"),
    ([{"row_number": 5, "target_stage": "No Such Stage"}], "no stage titled"),
    ([{"row_number": 2, "skip": True}, {"row_number": 2, "confirm_update": True}], "More than one decision"),
])
def test_decisions_the_file_or_board_reject(env, decisions, message):
    response = preview(env, decisions=decisions)
    assert response.status_code == 422 and message in response.json()["detail"]


def test_a_decision_that_does_not_fit_its_row_cannot_be_confirmed(env):
    body = previewed(env, decisions=[{"row_number": 2, "confirm_update": True}])
    assert body["confirmable"] is False and body["preview_token"] is None
    [row] = [r for r in body["rows"] if r["row_number"] == 2]
    assert row["action"] == "review" and "decision_not_applicable" in [r["code"] for r in row["reasons"]]


# --- confirmation ----------------------------------------------------------------------------

def test_confirm_imports_and_records_baselines(env):
    result = run(env, decisions=DECISIONS)
    batch = result["batch"]
    assert batch["status"] == "completed" and batch["username"] == "admin"
    assert (batch["created_count"], batch["updated_count"], batch["skipped_count"], batch["review_count"]) == (3, 0, 0, 1)
    assert len(result["created_card_ids"]) == 3 and result["review"] == 1
    with env.Session() as db:
        cards = db.query(Card).filter(Card.id.in_(result["created_card_ids"])).all()
        assert sorted(c.column.title for c in cards) == ["Award", "Lost", "Proposal"]
        for card in cards:
            assert card.bid_details is not None and card.source_ref.last_import_batch_id == batch["id"]
            assert not card.has_member_edits and card.import_changes == []
            [event_] = db.query(CardStageEvent).filter(CardStageEvent.card_id == card.id).all()
            assert (event_.event_type, event_.source) == ("baseline", "import")


def test_confirm_requires_explicit_confirmation(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    before = snapshot(env.path)
    for flag in ("false", "", "1", "True"):
        assert confirm(env, token, decisions=DECISIONS, confirm_flag=flag).status_code == 422
    assert snapshot(env.path) == before


def test_confirm_refuses_a_missing_forged_or_foreign_token(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    other_board_token = previewed(env, decisions=DECISIONS, board=env.other_board)["preview_token"]
    other_admin_token = previewed(env, decisions=DECISIONS, user="admin2")["preview_token"]
    before = snapshot(env.path)
    forged = create_purpose_token("1", "signup", timedelta(minutes=5))
    for bad in (None, "", "garbage", token[:-4] + "AAAA", forged, other_board_token, other_admin_token):
        response = confirm(env, bad, decisions=DECISIONS)
        assert response.status_code == 422, bad
        assert "preview" in response.json()["detail"]
    assert snapshot(env.path) == before


def test_confirm_refuses_another_file(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    before = snapshot(env.path)
    other = xlsx(*ROWS[:3], excel_row(**{"Código": "000199-01"}))
    response = confirm(env, token, data=other, decisions=DECISIONS)
    assert response.status_code == 422 and response.json()["detail"] == "This is not the file that was previewed"
    assert snapshot(env.path) == before


def test_confirm_refuses_other_decisions(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    before = snapshot(env.path)
    response = confirm(env, token, decisions=[*DECISIONS, {"row_number": 2, "skip": True}])
    assert response.status_code == 422 and "decisions" in response.json()["detail"]
    assert snapshot(env.path) == before


def test_same_bytes_under_another_name_are_the_same_file(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    response = env.client.post(
        f"/api/boards/{env.board}/imports",
        files={"file": ("copia.xlsx", xlsx(), XLSX)},
        data={"decisions": json.dumps(DECISIONS), "confirm": "true", "preview_token": token},
        headers=auth(env),
    )
    assert response.status_code == 201 and response.json()["batch"]["filename"] == "copia.xlsx"


def test_repeated_confirmation_writes_once(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    assert confirm(env, token, decisions=DECISIONS).status_code == 201
    after_first = snapshot(env.path)
    response = confirm(env, token, decisions=DECISIONS)
    assert response.status_code == 409
    assert snapshot(env.path) == after_first
    assert [b[0] for b in batches(env.path)] == ["completed"]


def test_confirm_refuses_when_the_board_changed_since_the_preview(env):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    with env.Session() as db:
        proposal = db.query(BoardColumn).filter_by(board_id=env.board, title="Proposal").one()
        proposal.title = "Quote"  # row 2 now has no target stage
        db.commit()
    before = snapshot(env.path)
    response = confirm(env, token, decisions=DECISIONS)
    assert response.status_code == 409 and "changed since the preview" in response.json()["detail"]
    assert snapshot(env.path) == before


def test_internal_duplicates_are_left_for_review(env):
    data = xlsx(excel_row(**{"Código": "000101-01"}), excel_row(**{"Código": "000101-01", "Cliente": "CLIENTE  FICTÍCIO"}))
    body = previewed(env, data=data)
    assert [r["required_decisions"] for r in body["rows"]] == [["resolve_duplicate"], ["resolve_duplicate"]]
    assert [r["duplicate_rows"] for r in body["rows"]] == [[3], [2]]
    result = run(env, data=data)
    assert result["created_card_ids"] == [] and result["review"] == 2
    result = run(env, data=data, decisions=[{"row_number": 3, "skip": True}])
    assert len(result["created_card_ids"]) == 1 and result["skipped"] == 1


def test_an_unchanged_repeat_is_a_no_op_without_a_batch(env):
    data = xlsx(excel_row(**{"Código": "000101-01"}))
    first = run(env, data)
    assert len(first["created_card_ids"]) == 1 and first["no_op"] is False
    before = snapshot(env.path)
    again = run(env, data)
    assert again["no_op"] is True and again["batch"] is None
    assert again["created_card_ids"] == [] and again["skipped"] == 1
    assert snapshot(env.path) == before
    assert [b[0] for b in batches(env.path)] == ["completed"]


def test_a_bid_already_imported_into_another_board_is_a_conflict(env):
    data = xlsx(ROWS[0])
    run(env, data=data)
    before = snapshot(env.path)
    response = preview(env, data=data, board=env.other_board)
    assert response.status_code == 409 and "another board" in response.json()["detail"]
    assert snapshot(env.path) == before


def test_a_concurrent_change_caught_by_the_service_is_a_conflict(env, monkeypatch):
    """Between the route's check and the write lock, another import lands: the
    service's own re-check refuses, rolls back and records a failed batch."""
    data = xlsx(ROWS[0])
    token = previewed(env, data=data)["preview_token"]
    real_apply = import_routes.apply_bid_import

    def apply_after_a_concurrent_import(db, read_result, preview_, decisions, board_id, user):
        with env.Session() as other:
            real_apply(other, read_result, preview_, decisions, board_id, other.get(User, user.id))
        return real_apply(db, read_result, preview_, decisions, board_id, user)

    monkeypatch.setattr(import_routes, "apply_bid_import", apply_after_a_concurrent_import)
    response = confirm(env, token, data=data)
    assert response.status_code == 409
    assert [b[:2] for b in batches(env.path)] == [("completed", None), ("failed", "conflict")]
    with env.Session() as db:
        assert db.query(Card).count() == 1


def test_an_unexpected_failure_rolls_everything_back(env, monkeypatch):
    token = previewed(env, decisions=DECISIONS)["preview_token"]
    before = snapshot(env.path, COMMERCIAL)
    real_write = bid_import_service._write_rows

    def write_then_fail(*args, **kwargs):
        real_write(*args, **kwargs)  # cards, details, refs and stage events flushed...
        raise RuntimeError("sqlite3 INSERT parameters: Projeto Teste, Cliente Ficticio")  # ...then a failure

    monkeypatch.setattr(bid_import_service, "_write_rows", write_then_fail)
    response = confirm(env, token, decisions=DECISIONS)
    assert response.status_code == 500
    assert response.json() == {"detail": "The import failed; nothing was written"}
    assert snapshot(env.path, COMMERCIAL) == before
    assert batches(env.path) == [("failed", "unexpected", "RuntimeError")]


# --- re-import, history and undo -------------------------------------------------------------

NEWER = {"Código": "000101-01", "Atualizado em": "01/04/2026", "Nome do projeto": "Projeto Renomeado"}


def test_reimport_update_goes_to_import_history_not_member_edits(env):
    run(env, data=xlsx(ROWS[0]))
    data = xlsx(excel_row(**NEWER))
    body = previewed(env, data=data)
    [row] = body["rows"]
    assert row["action"] == "update" and row["required_decisions"] == ["confirm_update"]
    assert {"field": "card.title", "current": "Projeto Teste", "incoming": "Projeto Renomeado"} in row["changed_fields"]
    result = run(env, data=data, decisions=[{"row_number": 2, "confirm_update": True}])
    assert result["batch"]["updated_count"] == 1
    with env.Session() as db:
        card = db.get(Card, result["updated_card_ids"][0])
        assert card.title == "Projeto Renomeado" and not card.has_member_edits
        [change] = card.import_changes
        assert json.loads(change.changes)["card.title"] == {"old": "Projeto Teste", "new": "Projeto Renomeado"}
        # Still only the baseline: an update without a move is no commercial event.
        assert [e.event_type for e in db.query(CardStageEvent).filter_by(card_id=card.id)] == ["baseline"]


def test_undo_refuses_to_overwrite_an_import_update(env):
    created = run(env, data=xlsx(ROWS[0]))["created_card_ids"][0]
    edit = env.client.put(f"/api/boards/{env.board}/cards/{created}", json={"details": "Nota manual"}, headers=auth(env))
    assert edit.status_code == 200
    data = xlsx(excel_row(**{**NEWER, "Descrição da prospecção": "Nova descricao"}))
    run(env, data=data, decisions=[{"row_number": 2, "confirm_update": True}])
    response = env.client.post(f"/api/boards/{env.board}/undo", headers=auth(env))
    assert response.status_code == 409
    with env.Session() as db:
        assert db.get(Card, created).details == "Nova descricao"


# --- batch history ------------------------------------------------------------------------------

def test_list_and_details(env, monkeypatch):
    first = run(env, data=xlsx(ROWS[0]))["batch"]
    run(env, data=xlsx(ROWS[1]))
    monkeypatch.setattr(bid_import_service, "_write_rows", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    data = xlsx(ROWS[2])
    token = previewed(env, data=data, decisions=[{"row_number": 2, "confirm_proposed_stage": True}])["preview_token"]
    assert confirm(env, token, data=data, decisions=[{"row_number": 2, "confirm_proposed_stage": True}]).status_code == 500
    other = previewed(env, data=xlsx(excel_row(**{"Código": "000777-01"})), board=env.other_board)["preview_token"]

    listing = env.client.get(f"/api/boards/{env.board}/imports", headers=auth(env)).json()
    assert listing["total"] == 3 and (listing["limit"], listing["offset"]) == (50, 0)
    assert [b["status"] for b in listing["items"]] == ["failed", "completed", "completed"]
    ids = [b["id"] for b in listing["items"]]
    assert ids == sorted(ids, reverse=True)
    page = env.client.get(f"/api/boards/{env.board}/imports?limit=1&offset=1", headers=auth(env)).json()
    assert [b["id"] for b in page["items"]] == [ids[1]] and page["total"] == 3
    assert env.client.get(f"/api/boards/{env.board}/imports?limit=0", headers=auth(env)).status_code == 422
    assert other  # previewing on the other board wrote no batch
    assert env.client.get(f"/api/boards/{env.other_board}/imports", headers=auth(env)).json()["total"] == 0

    detail = env.client.get(f"/api/imports/{first['id']}", headers=auth(env)).json()
    assert detail["board_id"] == env.board and detail["status"] == "completed" and detail["created_count"] == 1
    assert detail["mapping"]["source_system"] == "salcomp-bids" and detail["card_changes_count"] == 0
    assert [s["title"] for s in detail["mapping"]["stages"]] == [title for title, _ in PIPELINE_STAGES]
    failed = env.client.get(f"/api/imports/{ids[0]}", headers=auth(env)).json()
    assert (failed["failure_kind"], failed["failure_reason"], failed["created_count"]) == ("unexpected", "RuntimeError", 0)


def test_batches_of_other_boards_never_show_up_in_a_listing(env):
    run(env, data=xlsx(ROWS[0]))
    body = previewed(env, data=xlsx(excel_row(**{"Código": "000777-01"})), board=env.other_board)
    response = env.client.post(
        f"/api/boards/{env.other_board}/imports",
        files={"file": ("relatorio-leads.xlsx", xlsx(excel_row(**{"Código": "000777-01"})), XLSX)},
        data={"decisions": "[]", "confirm": "true", "preview_token": body["preview_token"]},
        headers=auth(env),
    )
    assert response.status_code == 201
    other_id = response.json()["batch"]["id"]
    mine = env.client.get(f"/api/boards/{env.board}/imports", headers=auth(env)).json()["items"]
    theirs = env.client.get(f"/api/boards/{env.other_board}/imports", headers=auth(env)).json()["items"]
    assert other_id not in [b["id"] for b in mine] and [b["id"] for b in theirs] == [other_id]
    # Boards are shared and imports are admin-only, so any admin may read any batch by id;
    # the batch says which board it belongs to.
    assert env.client.get(f"/api/imports/{other_id}", headers=auth(env, "admin2")).json()["board_id"] == env.other_board


def test_batch_responses_carry_no_personal_or_internal_data(env):
    batch_id = run(env, decisions=DECISIONS)["batch"]["id"]
    for url in (f"/api/boards/{env.board}/imports", f"/api/imports/{batch_id}"):
        text = env.client.get(url, headers=auth(env)).text
        for secret in ("admin@example.test", "Member Person", "password", "Pessoa A", "Pessoa B", "Projeto Teste",
                       "Cliente Ficticio", "mapping_json"):
            assert secret not in text, (url, secret)


# --- temporary files of the upload are closed explicitly -----------------------------

class _Parsers(list):
    """Every MultiPartParser _read_form builds, to inspect its temporary files."""

    @property
    def files(self):
        return [f for parser in self for f in parser._files_to_close_on_error]


@pytest.fixture
def parsers(monkeypatch):
    seen = _Parsers()

    class Recording(import_routes.MultiPartParser):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            seen.append(self)

    monkeypatch.setattr(import_routes, "MultiPartParser", Recording)
    return seen


def _read(body_chunks, content_length, content_type=None, name="file"):
    """Run _read_form on a request whose body arrives as the given chunks; an
    Exception instance in the list is raised by receive()."""
    import asyncio

    from starlette.requests import Request

    chunks = list(body_chunks)

    async def receive():
        chunk = chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    scope = {
        "type": "http", "method": "POST", "path": "/", "query_string": b"",
        "headers": [
            (b"content-type", (content_type or _multipart(b"")[1]).encode()),
            (b"content-length", str(content_length).encode()),
        ],
    }
    return asyncio.run(import_routes._read_form(Request(scope, receive)))


def _head(name="file"):
    return (
        f'--testboundary\r\nContent-Disposition: form-data; name="{name}"; filename="x.xlsx"\r\n'
        f"Content-Type: {XLSX}\r\n\r\nPK"
    ).encode()


def test_files_are_closed_when_the_upload_exceeds_the_limit_mid_stream(parsers, monkeypatch):
    monkeypatch.setattr(import_routes, "LIMITS", replace(import_routes.LIMITS, max_file_bytes=1000))
    monkeypatch.setattr(import_routes, "FORM_OVERHEAD_BYTES", 1000)
    # The file part is open (and written) when the cut-off happens.
    with pytest.raises(import_routes.HTTPException) as exc:
        _read([_head(), b"\0" * 1000, b"\0" * 1000, b"\0" * 1000], content_length=500)
    assert exc.value.status_code == 413
    assert len(parsers.files) == 1
    assert all(f.closed for f in parsers.files)


def test_files_are_closed_when_reading_the_body_fails(parsers):
    with pytest.raises(ConnectionResetError):
        _read([_head(), b"\0" * 10, ConnectionResetError("client went away")], content_length=500)
    assert len(parsers.files) == 1
    assert all(f.closed for f in parsers.files)


def test_files_are_closed_when_the_multipart_body_is_rejected(parsers):
    # Two file parts: Starlette refuses the second (max_files=1); still a 400.
    body = _head() + b"\r\n" + _head() + b"\r\n--testboundary--\r\n"
    with pytest.raises(import_routes.HTTPException) as exc:
        _read([body], content_length=len(body))
    assert exc.value.status_code == 400
    assert all(f.closed for f in parsers.files)


def test_files_are_closed_when_the_file_field_is_missing(parsers):
    body = _head(name="other") + b"\r\n--testboundary--\r\n"
    with pytest.raises(import_routes.HTTPException) as exc:
        _read([body], content_length=len(body))
    assert exc.value.status_code == 422
    assert len(parsers.files) == 1
    assert all(f.closed for f in parsers.files)


def test_files_are_closed_after_a_successful_read(parsers):
    body = _head() + b"\r\n--testboundary--\r\n"
    data, filename, decisions, _ = _read([body], content_length=len(body))
    assert (data, filename, decisions) == (b"PK", "x.xlsx", [])
    assert all(f.closed for f in parsers.files)
