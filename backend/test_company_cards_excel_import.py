"""Cards workbook import (preview + all-or-nothing confirm) through the company routes.

Disposable tmp_path identity and company databases and in-memory workbooks only; the Bid importer is not involved.
Board 1 of the rig has columns First (id 1) and Second (id 2) and two unassigned cards in First.
"""
import io
import json
import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook

import cards_excel_import
import identity_service as svc
import excel_contract as contract
from company_schema import validate_company_schema
from create_company_db import create_company_database
from database_targets import company_database_path
from excel_cards_writer import build_workbook
from init_company_excel_imports import init_company_excel_imports
from test_company_admin_products import company_admin
from test_company_card_writes import NOW, admin, member
from test_company_card_writes import rig  # noqa: F401  (fixture)
from test_company_concurrency_integrity import Call, Gate, TIMEOUT, interleave, warm_up

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PREVIEW, CONFIRM = "/excel/import/preview", "/excel/import/confirm"


def row(**values):
    base = {"title": "Lead"}
    base.update(values)
    return base


def book(rows):
    return build_workbook(rows)


def raw_book(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Cards"
    ws.append(headers)
    for values in rows:
        ws.append(values)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def upload(content):
    return {"file": ("cards.xlsx", content, XLSX)}


def preview(rig, token, content, slug="lity", board=1):
    return rig.call("post", PREVIEW, token, slug=slug, files=upload(content)) if board == 1 else rig.client.post(
        f"/api/companies/{slug}/boards/{board}/excel/import/preview",
        headers={"Authorization": f"Bearer {token}"}, files=upload(content))


def confirm(rig, token, content, digest, slug="lity", **extra):
    return rig.call("post", CONFIRM, token, slug=slug, files=upload(content),
                    data={"confirm": "true", "plan_digest": digest, **extra})


def apply(rig, token, content, slug="lity"):
    """Preview then confirm; returns (preview body, confirm response)."""
    body = preview(rig, token, content, slug).json()
    return body, confirm(rig, token, content, body["plan_digest"], slug)


def cards(rig, slug="lity"):
    return rig.sql("SELECT id, title, column_id, position, salesperson_id, deal_value, deal_value_next_year, "
                   "product_line, confidence_level FROM cards ORDER BY id", slug=slug)


def counts(rig, slug="lity"):
    return tuple(rig.sql(f"SELECT COUNT(*) FROM {t}", slug=slug)[0][0]
                 for t in ("cards", "card_excel_imports", "audit_events", "card_stage_events", "card_edit_history"))


def person(rig, email="ana@example.com", slug="lity"):
    """A salesperson who can be assigned: central member plus the local reference row."""
    uid, _ = member(rig, email, slug=slug)
    rig.sql("INSERT OR IGNORE INTO users (id, username, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (uid, email.split("@")[0], NOW, NOW), slug)
    return uid


def rows_by_number(body):
    return {r["row"]: r for r in body["rows"]}


# --- preview ------------------------------------------------------------------------------------

def test_preview_classifies_rows_and_writes_nothing(rig):
    token = admin(rig)
    before = counts(rig)
    snapshot = cards(rig)
    content = book([row(title="New A", stage="Second"), row(title="New B"), row(card_id=1, title="Renamed"),
                    row(card_id=2, title="card 2", stage="First")])
    r = preview(rig, token, content)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["creates"] == 2 and body["summary"]["updates"] == 1 and body["summary"]["unchanged"] == 1
    assert body["summary"]["invalid"] == 0 and body["confirmable"] is True and body["blocked_reason"] is None
    by_row = rows_by_number(body)
    assert by_row[2]["action"] == "create" and by_row[2]["stage"] == "Second"
    assert by_row[3]["action"] == "create" and by_row[3]["stage"] == "First"
    assert by_row[3]["warnings"] == [] or by_row[3]["warnings"][0]["code"] == "stage_defaulted"
    assert by_row[4]["action"] == "update" and by_row[4]["changed"] == ["title"]
    assert by_row[5]["action"] == "unchanged"
    assert cards(rig) == snapshot and counts(rig)[:2] == before[:2] and counts(rig)[3:] == before[3:]
    assert rig.sql("SELECT COUNT(*) FROM card_excel_imports") == [(0,)]


def test_preview_blank_stage_on_a_new_card_warns_and_uses_the_first_stage(rig):
    body = preview(rig, admin(rig), book([row(title="X", stage="")])).json()
    first = body["rows"][0]
    assert first["stage"] == "First" and [w["code"] for w in first["warnings"]] == ["stage_defaulted"]
    assert body["summary"]["warnings"] >= 1


# --- create / update ----------------------------------------------------------------------------

def test_valid_multi_card_creation_applies_everything_in_one_transaction(rig):
    token = admin(rig)
    uid = person(rig)
    content = book([
        row(title="One", company="Acme", contact="Bob", details="d", deal_value_usd=Decimal("1000.50"),
            deal_value_next_year_usd=Decimal("250"), confidence_level="HIGH", expected_close_date=date(2026, 12, 31),
            product_line="Chargers", lead_source="Trade show", salesperson_email="ana@example.com", stage="Second"),
        row(title="Two"),
        row(title="Three", stage="Second"),
    ])
    body, r = apply(rig, token, content)
    assert r.status_code == 201, r.text
    assert r.json()["created"] == 3 and r.json()["updated"] == 0 and r.json()["applied"] is True
    got = {c[1]: c for c in cards(rig)}
    one = got["One"]
    assert one[2] == 2 and one[4] == uid and one[5] == 1000.5 and one[6] == 250.0 and one[7] == "Chargers" and one[8] == "HIGH"
    assert got["Two"][2] == 1 and got["Two"][4] is None and got["Two"][6] == 0
    assert (got["One"][3], got["Three"][3]) == (0, 1)  # appended to the end of Second, in file order
    assert got["Two"][3] == 2  # after the two seeded cards (positions 1 and 2)
    audit = rig.sql("SELECT action FROM audit_events ORDER BY id")
    assert audit.count(("create",)) == 3 and audit.count(("assign",)) == 1
    assert rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE event_type = 'created'")[0][0] >= 3
    ledger = rig.sql("SELECT created_count, updated_count, repeat_guard IS NOT NULL, filename FROM card_excel_imports")
    assert ledger == [(3, 0, 1, "cards.xlsx")]


def test_update_by_card_id_changes_only_what_differs_and_moves_the_card(rig):
    token = admin(rig)
    uid = person(rig)
    rig.sql("UPDATE cards SET company = 'Old', deal_value = 5, deal_value_next_year = 7 WHERE id = 1")
    content = book([
        row(card_id=1, title="Card one", company="New Co", deal_value_usd=Decimal("10.25"),
            deal_value_next_year_usd=Decimal("0"), salesperson_email="ana@example.com", stage="Second"),
        row(card_id=2, title="card 2", stage="First"),
    ])
    body, r = apply(rig, token, content)
    assert r.status_code == 201 and r.json()["created"] == 0 and r.json()["updated"] == 1 and r.json()["unchanged"] == 1
    c1 = rig.sql("SELECT title, company, deal_value, deal_value_next_year, salesperson_id, column_id, position, currency "
                 "FROM cards WHERE id = 1")[0]
    assert c1 == ("Card one", "New Co", 10.25, 0.0, uid, 2, 0, "USD")
    assert rig.sql("SELECT column_id, position FROM cards WHERE id = 2") == [(1, 0)]  # gap closed in First
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]
    assert rig.sql("SELECT action FROM audit_events") == [("assign",)]
    entry = rig.sql("SELECT created_count, updated_count, repeat_guard, changes_json FROM card_excel_imports")[0]
    assert entry[:2] == (0, 1) and len(entry[2]) == 64  # every import carries the repeat guard
    changes = json.loads(entry[3])
    assert changes["created"] == [] and changes["updated"]["1"]["stage"] == {"old": "First", "new": "Second"}
    assert rig.sql("SELECT COUNT(*) FROM card_stage_events WHERE card_id = 1 AND event_type = 'moved'") == [(1,)]


def test_blank_cells_clear_present_columns_and_absent_columns_are_left_alone(rig):
    token = admin(rig)
    rig.sql("UPDATE cards SET company = 'Keep', contact = 'Cara', details = 'text', deal_value = 5, "
            "deal_value_next_year = 7, product_line = 'Chargers', confidence_level = 'LOW' WHERE id = 1")
    # Only title, contact and deal_value_next_year_usd columns: the rest of the card must not move.
    content = raw_book(["card_id", "title", "contact", "deal_value_next_year_usd"], [[1, "card 1", None, None]])
    body, r = apply(rig, token, content)
    assert r.status_code == 201, r.text
    assert rig.sql("SELECT company, contact, details, deal_value, deal_value_next_year, product_line, confidence_level "
                   "FROM cards WHERE id = 1") == [("Keep", None, "text", 5.0, 0.0, "Chargers", "LOW")]


def test_deal_year_and_derived_total_are_never_written(rig):
    token = admin(rig)
    year = rig.sql("SELECT deal_year FROM cards WHERE id = 1")[0][0]
    content = raw_book(["card_id", "title", "deal_value_usd", "deal_total_usd", "deal_year"],
                       [[1, "card 1", 100, "not-a-number", 1999], [None, "Fresh", 5, 999999, 2001]])
    body, r = apply(rig, token, content)
    assert r.status_code == 201, r.text
    assert rig.sql("SELECT deal_year FROM cards WHERE id = 1") == [(year,)]
    fresh = rig.sql("SELECT deal_value, deal_year FROM cards WHERE title = 'Fresh'")[0]
    assert fresh[0] == 5.0 and fresh[1] != 2001


# --- identity of cards --------------------------------------------------------------------------

def test_unknown_card_id_is_rejected_and_never_becomes_a_create(rig):
    token = admin(rig)
    body = preview(rig, token, book([row(card_id=999, title="Ghost"), row(title="ok")])).json()
    assert body["rows"][0]["action"] == "invalid" and body["confirmable"] is False
    assert [e["code"] for e in body["rows"][0]["errors"]] == ["card_not_found"]
    before = counts(rig)
    r = confirm(rig, token, book([row(card_id=999, title="Ghost"), row(title="ok")]), body["plan_digest"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "validation_errors"
    assert counts(rig) == before


def test_other_board_and_other_company_cards_look_exactly_like_missing_ones(rig):
    token = admin(rig)
    rig.sql("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (2, 7, 'other', ?, ?)", (NOW, NOW))
    rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
            "VALUES (20, 2, 'Elsewhere', 0, 'open', ?, ?)", (NOW, NOW))
    rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) VALUES (50, 20, 'foreign', 0, ?, ?)",
            (NOW, NOW))
    rig.sql("INSERT INTO cards (id, column_id, title, position, created_at, updated_at) VALUES (60, 1, 'x', 9, ?, ?)",
            (NOW, NOW), slug="valor-log")
    before = {s: rig.digest(s) for s in ("valor-log", "salcomp")}
    responses = {}
    for label, card_id in (("missing", 999), ("other_board", 50), ("other_company", 60)):
        body = preview(rig, token, book([row(card_id=card_id, title="Hijack")])).json()
        responses[label] = body["rows"][0]["errors"]
    assert responses["missing"] == responses["other_board"] == responses["other_company"]
    assert responses["missing"][0]["code"] == "card_not_found"
    assert rig.sql("SELECT title FROM cards WHERE id = 50") == [("foreign",)]
    assert {s: rig.digest(s) for s in before} == before  # no other company database was touched
    # the same id is valid in the board that owns it
    assert preview(rig, token, book([row(card_id=50, title="Renamed")]), board=2).json()["rows"][0]["action"] == "update"


def test_duplicate_card_id_in_one_workbook_rejects_every_use(rig):
    token = admin(rig)
    body = preview(rig, token, book([row(card_id=1, title="a"), row(card_id=1, title="b"), row(card_id=2, title="c")])).json()
    first, second, third = body["rows"]
    assert [e["code"] for e in first["errors"]] == ["duplicate_card_id"] == [e["code"] for e in second["errors"]]
    assert third["errors"] == [] and body["confirmable"] is False


# --- reference resolution and value validation ---------------------------------------------------

def test_stage_product_and_salesperson_failures_are_row_errors(rig):
    token = admin(rig)
    rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
            "VALUES (3, 1, 'second', 2, 'open', ?, ?)", (NOW, NOW))  # same title as column 2 once case is ignored
    _, visitor_token = member(rig, "vis@example.com", role="visitor")
    gone, _ = member(rig, "gone@example.com")  # a member whose central membership was deactivated
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", gone)
    content = book([
        row(title="bad stage", stage="Nowhere"),
        row(title="ambiguous", stage="Second"),
        row(title="bad product", product_line="Rockets"),
        row(title="unknown person", salesperson_email="nobody@example.com"),
        row(title="visitor", salesperson_email="vis@example.com"),
        row(title="deactivated", salesperson_email="gone@example.com"),
        row(title="fine", stage="First", product_line="chargers"),
    ])
    body = preview(rig, token, content).json()
    codes = [[e["code"] for e in r["errors"]] for r in body["rows"]]
    assert codes == [["unknown_stage"], ["ambiguous_stage"], ["unknown_product_line"], ["salesperson_unavailable"],
                     ["salesperson_unavailable"], ["salesperson_unavailable"], []]
    messages = {e["message"] for r in body["rows"][3:6] for e in r["errors"]}
    assert len(messages) == 1  # the three salesperson failures cannot be told apart
    assert body["rows"][6]["action"] == "create" and body["confirmable"] is False


def test_invited_salesperson_without_a_local_row_can_be_assigned_by_import(rig):
    """B2: an accepted, active salesperson who never wrote in the company is not rejected; the row is created once."""
    token = admin(rig)
    uid, _ = member(rig, "ana@example.com")
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)) == [(0,)]
    content = book([row(title="One", salesperson_email="ana@example.com"), row(title="Two", salesperson_email="ANA@example.com")])
    body, response = apply(rig, token, content)
    assert body["confirmable"] is True and response.status_code == 201
    assert [c[4] for c in cards(rig) if c[1] in ("One", "Two")] == [uid, uid]
    assert rig.sql("SELECT id, username FROM users WHERE id = ?", (uid,)) == [(uid, "ana@example.com")]
    # a second import syncs again without duplicating the reference
    apply(rig, token, book([row(title="Three", salesperson_email="ana@example.com")]))
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (uid,)) == [(1,)]


def test_sync_creates_references_only_for_eligible_people_and_is_idempotent(rig):
    token = admin(rig)
    ana, _ = member(rig, "ana@example.com")
    vis, _ = member(rig, "vis@example.com", role="visitor")
    other, _ = member(rig, "far@example.com", slug="valor-log")
    gone, _ = member(rig, "gone@example.com")
    svc.deactivate_membership(rig.e.session, rig.e.admin, "lity", gone)
    for _ in range(2):  # the preview syncs too, so repeated calls must not change anything
        assert preview(rig, token, book([row(title="x")])).status_code == 200
    ids = {r[0] for r in rig.sql("SELECT id FROM users")}
    assert ana in ids and rig.e.admin in ids  # eligible: the salesperson and the global admin
    assert not ({vis, other, gone} & ids)  # visitor, other company, deactivated: no reference, no access
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (ana,)) == [(1,)]
    assert rig.sql("SELECT COUNT(*) FROM users WHERE id = ?", (other,), slug="valor-log") == [(0,)]


def test_an_invalid_row_still_blocks_the_import_after_the_sync(rig):
    token = admin(rig)
    uid, _ = member(rig, "ana@example.com")
    before = counts(rig)
    content = book([row(title="ok", salesperson_email="ana@example.com"), row(title="bad", salesperson_email="nobody@example.com")])
    body = preview(rig, token, content).json()
    assert body["confirmable"] is False
    assert confirm(rig, token, content, body["plan_digest"]).status_code != 201
    assert counts(rig) == before  # no card, ledger, audit or event row; only the reference row may exist


def test_product_catalog_name_is_stored_in_canonical_case(rig):
    token = admin(rig)
    body, r = apply(rig, token, book([row(title="p", product_line="power SUPPLIES")]))
    assert r.status_code == 201
    assert rig.sql("SELECT product_line FROM cards WHERE title = 'p'") == [("Power supplies",)]


def test_invalid_amounts_dates_and_probability_are_row_errors(rig):
    token = admin(rig)
    headers = ["card_id", "title", "deal_value_usd", "deal_value_next_year_usd", "expected_close_date", "probability_level"]
    content = raw_book(headers, [
        [None, "negative", "-5", None, None, None],
        [None, "text amount", "abc", None, None, None],
        [None, "three decimals", "1.234", None, None, None],
        [None, "huge", "9" * 20, None, None, None],
        [None, "bad date", None, None, "31/12/2026", None],
        [None, "bad level", None, None, None, "MAYBE"],
        [None, "next year negative", None, "-1", None, None],
        [None, "ok", "1.5", "2", "2026-02-03", "low"],
    ])
    body = preview(rig, token, content).json()
    codes = [[e["code"] for e in r["errors"]] for r in body["rows"]]
    assert codes == [["negative_amount"], ["invalid_amount"], ["too_many_decimals"], ["amount_too_large"], ["invalid_date"],
                     ["invalid_probability_level"], ["negative_amount"], []]
    assert not any("abc" in json.dumps(r) or "MAYBE" in json.dumps(r) for r in body["rows"])  # values never echoed


def test_formula_cells_are_row_errors_and_legacy_columns_reject_the_file(rig):
    token = admin(rig)
    formula = preview(rig, token, raw_book(["card_id", "title", "deal_value_usd"], [[None, "f", "=1+1"], [None, "g", 3]])).json()
    assert [e["code"] for e in formula["rows"][0]["errors"]] == ["formula_not_allowed"] and formula["confirmable"] is False
    for legacy in ("revenue_current_year", "bid_plant", "next_step", "next_step_due", "something_else"):
        r = preview(rig, token, raw_book(["card_id", "title", legacy], [[None, "x", "1"]]))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "file_rejected"
        assert r.json()["detail"]["file_errors"][0]["code"] in ("forbidden_column", "unknown_column")
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def test_files_that_are_not_workbooks_or_have_no_rows_are_rejected(rig):
    token = admin(rig)
    assert preview(rig, token, b"not a workbook").status_code == 422
    assert preview(rig, token, book([])).status_code == 422  # headers only
    r = rig.call("post", PREVIEW, token)  # no body at all
    assert r.status_code in (411, 422)


def test_mixed_valid_and_invalid_rows_block_the_whole_confirmation(rig):
    token = admin(rig)
    content = book([row(title="good one"), row(title="bad", stage="Nowhere"), row(card_id=1, title="renamed")])
    body = preview(rig, token, content).json()
    assert body["summary"]["invalid"] == 1 and body["confirmable"] is False
    before = counts(rig)
    r = confirm(rig, token, content, body["plan_digest"])
    assert r.status_code == 422 and counts(rig) == before
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("card 1",)]


# --- atomicity ------------------------------------------------------------------------------------

def test_a_failure_halfway_rolls_back_every_create_update_and_the_ledger(rig, monkeypatch):
    token = admin(rig)
    content = book([row(card_id=1, title="Renamed", stage="Second"), row(title="n1"), row(title="n2"), row(title="n3")])
    body = preview(rig, token, content).json()
    before, snapshot = counts(rig), cards(rig)
    real, calls = cards_excel_import.audit_new_card_owner, []

    def explode(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("disk exploded: secret-path /private/db")
        return real(*args, **kwargs)

    monkeypatch.setattr(cards_excel_import, "audit_new_card_owner", explode)
    r = rig.client.post("/api/companies/lity/boards/1" + CONFIRM, headers={"Authorization": f"Bearer {token}"},
                        files=upload(content), data={"confirm": "true", "plan_digest": body["plan_digest"]})
    assert r.status_code == 500
    assert r.json()["detail"] == {"code": "import_failed", "message": "The import failed and nothing was changed", "applied": False}
    assert "secret" not in r.text and "exploded" not in r.text
    assert len(calls) == 3
    assert cards(rig) == snapshot and counts(rig) == before
    monkeypatch.undo()
    assert confirm(rig, token, content, body["plan_digest"]).status_code == 201  # a clean retry works


def test_a_ledger_failure_rolls_back_the_card_changes(rig, monkeypatch):
    token = admin(rig)
    content = book([row(title="n1"), row(card_id=1, title="Renamed")])
    body = preview(rig, token, content).json()
    before, snapshot = counts(rig), cards(rig)

    def broken():
        raise sqlite3.DatabaseError("ledger write failed")

    monkeypatch.setattr(cards_excel_import.ledger, "now", broken)  # called when the ledger row is built, after the cards
    r = confirm(rig, token, content, body["plan_digest"])
    assert r.status_code == 500 and cards(rig) == snapshot and counts(rig) == before


# --- repeated, modified and concurrent imports -----------------------------------------------------

def test_the_same_workbook_of_new_cards_cannot_be_applied_twice(rig):
    token = admin(rig)
    content = book([row(title="N1"), row(title="N2", company="Acme")])
    body, first = apply(rig, token, content)
    assert first.status_code == 201
    after_first = counts(rig)
    again = preview(rig, token, content).json()
    assert again["confirmable"] is False and again["blocked_reason"] == "already_imported"
    assert again["already_imported"]["created"] == 2
    r = confirm(rig, token, content, body["plan_digest"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_imported"
    assert r.json()["detail"]["import_summary"]["import_id"] == first.json()["import_id"]
    assert counts(rig) == after_first and rig.sql("SELECT COUNT(*) FROM cards") == [(4,)]


def test_row_order_filename_and_deal_total_do_not_make_a_workbook_new(rig):
    token = admin(rig)
    a = book([row(title="N1"), row(title="N2")])
    assert apply(rig, token, a)[1].status_code == 201
    reordered = book([row(title="N2", deal_total_usd=Decimal("42")), row(title="N1")])
    body = rig.call("post", PREVIEW, token, files={"file": ("renamed-copy.xlsx", reordered, XLSX)}).json()
    assert body["blocked_reason"] == "already_imported"


def test_a_workbook_with_changed_business_data_is_a_new_import(rig):
    token = admin(rig)
    assert apply(rig, token, book([row(title="N1"), row(title="N2")]))[1].status_code == 201
    changed = book([row(title="N1"), row(title="N2", deal_value_usd=Decimal("10"))])
    body, r = apply(rig, token, changed)
    assert body["confirmable"] is True and r.status_code == 201 and r.json()["created"] == 2
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(6,)]
    assert rig.sql("SELECT COUNT(*) FROM card_excel_imports") == [(2,)]


def test_the_guard_is_per_board(rig):
    token = admin(rig)
    rig.sql("INSERT INTO boards (id, user_id, title, created_at, updated_at) VALUES (2, 7, 'other', ?, ?)", (NOW, NOW))
    rig.sql("INSERT INTO columns (id, board_id, title, position, stage_kind, created_at, updated_at) "
            "VALUES (20, 2, 'First', 0, 'open', ?, ?)", (NOW, NOW))
    content = book([row(title="N1")])
    assert apply(rig, token, content)[1].status_code == 201
    body = preview(rig, token, content, board=2).json()
    assert body["confirmable"] is True and body["already_imported"] is None


def test_a_b_a_the_second_import_of_a_is_blocked_before_any_change(rig):
    token = admin(rig)
    a = book([row(card_id=1, title="Version A")])
    b = book([row(card_id=1, title="Version B")])
    assert apply(rig, token, a)[1].status_code == 201
    assert apply(rig, token, b)[1].status_code == 201
    before, snapshot = counts(rig), cards(rig)
    body = preview(rig, token, a).json()  # A would now change the title back, yet it is the same logical workbook
    assert body["summary"]["updates"] == 1 and body["confirmable"] is False
    assert body["blocked_reason"] == "already_imported" and body["already_imported"]["updated"] == 1
    r = confirm(rig, token, a, body["plan_digest"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_imported"
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("Version B",)]
    assert counts(rig) == before and cards(rig) == snapshot


def test_an_update_workbook_with_changed_data_is_still_importable(rig):
    token = admin(rig)
    assert apply(rig, token, book([row(card_id=1, title="Version A")]))[1].status_code == 201
    assert apply(rig, token, book([row(card_id=1, title="Version A", company="Acme")]))[1].status_code == 201


def test_fingerprint_ignores_informational_columns_and_their_presence(rig):
    token = admin(rig)
    full = raw_book(["card_id", "title", "deal_value_usd", "deal_total_usd", "deal_year"], [[1, "FP", 10, 10, 2026]])
    bare = raw_book(["card_id", "title", "deal_value_usd"], [[1, "FP", 10]])
    other_year = raw_book(["card_id", "title", "deal_value_usd", "deal_year"], [[1, "FP", 10, 1999]])
    fps = {preview(rig, token, c).json()["plan_digest"] for c in (full, bare)}
    assert len(fps) == 1
    assert apply(rig, token, full)[1].status_code == 201
    for twin in (bare, other_year):
        assert preview(rig, token, twin).json()["blocked_reason"] == "already_imported"


def test_fingerprint_changes_with_a_business_value_or_a_meaningful_column():
    def read(headers, rows):
        from commercial_excel_reader import read_commercial_excel
        return read_commercial_excel(raw_book(headers, rows), "x.xlsx")
    base = cards_excel_import.fingerprint("lity", 1, read(["card_id", "title", "deal_value_usd"], [[1, "T", 10]]))
    assert base == cards_excel_import.fingerprint("lity", 1, read(["deal_value_usd", "title", "card_id"], [[10, "T", 1]]))
    assert base != cards_excel_import.fingerprint("lity", 1, read(["card_id", "title", "deal_value_usd"], [[1, "T", 11]]))
    assert base != cards_excel_import.fingerprint("lity", 1, read(["card_id", "title"], [[1, "T"]]))  # absent != blank
    assert base != cards_excel_import.fingerprint("lity", 2, read(["card_id", "title", "deal_value_usd"], [[1, "T", 10]]))
    assert base != cards_excel_import.fingerprint("other", 1, read(["card_id", "title", "deal_value_usd"], [[1, "T", 10]]))


def test_workbook_without_a_card_id_column_is_rejected_and_creates_nothing(rig):
    token = admin(rig)
    before = counts(rig)
    for call in (lambda: preview(rig, token, raw_book(["title"], [["Would be new"]])),
                 lambda: confirm(rig, token, raw_book(["title"], [["Would be new"]]), "x")):
        r = call()
        assert r.status_code == 422 and r.json()["detail"]["code"] == "file_rejected"
        assert [(e["code"], e["column"]) for e in r.json()["detail"]["file_errors"]] == [("missing_column", "card_id")]
    assert counts(rig) == before


def test_blank_card_id_cells_still_create_when_the_column_exists(rig):
    token = admin(rig)
    body, r = apply(rig, token, raw_book(["card_id", "title"], [[None, "Made"], [None, "Made too"]]))
    assert r.status_code == 201 and r.json()["created"] == 2


def test_blank_present_cells_clear_and_blank_salesperson_unassigns(rig):
    token = admin(rig)
    uid = person(rig)
    rig.sql("UPDATE cards SET company = 'Co', contact = 'Cara', details = 'd', deal_value = 5, deal_value_next_year = 7, "
            "product_line = 'Chargers', confidence_level = 'LOW', lead_source = 'Trade show', salesperson_id = ?, "
            "expected_close_date = '2026-01-02' WHERE id = 1", (uid,))
    cols = ["card_id", "title", "company", "contact", "details", "deal_value_usd", "deal_value_next_year_usd",
            "probability_level", "expected_close_date", "product_line", "lead_source", "salesperson_email", "stage"]
    content = raw_book(cols, [[1, "card 1"] + [None] * 11])
    body = preview(rig, token, content).json()
    diffs = {d["field"]: d for d in rows_by_number(body)[2]["diffs"]}
    assert diffs["salesperson_id"] == {"field": "salesperson_id", "old": "ana@example.com", "new": None}
    assert diffs["deal_value"] == {"field": "deal_value", "old": "5.00", "new": None}
    assert diffs["deal_value_next_year"] == {"field": "deal_value_next_year", "old": "7.00", "new": "0.00"}
    assert "stage" not in diffs and set(diffs) >= {"company", "contact", "details", "confidence_level", "lead_source"}
    assert confirm(rig, token, content, body["plan_digest"]).status_code == 201
    assert rig.sql("SELECT company, contact, details, deal_value, deal_value_next_year, product_line, confidence_level, "
                   "lead_source, salesperson_id, expected_close_date, column_id FROM cards WHERE id = 1") == [
        (None, None, None, None, 0.0, None, None, None, None, None, 1)]


def test_blank_title_stays_a_blocking_error(rig):
    body = preview(rig, admin(rig), raw_book(["card_id", "title"], [[1, None]])).json()
    assert body["confirmable"] is False and body["rows"][0]["errors"][0]["code"] == "required_value_missing"


def test_preview_shows_old_and_new_values_for_every_changed_field_including_stage(rig):
    token = admin(rig)
    rig.sql("UPDATE cards SET company = 'Old Co', deal_value = 5 WHERE id = 1")
    content = book([row(card_id=1, title="card 1", company="New Co", deal_value_usd=Decimal("10.5"), stage="Second")])
    body = preview(rig, token, content).json()
    row2 = rows_by_number(body)[2]
    assert row2["diffs"] == [
        {"field": "company", "old": "Old Co", "new": "New Co"},
        {"field": "deal_value", "old": "5.00", "new": "10.50"},
        {"field": "stage", "old": "First", "new": "Second"},
    ]
    assert row2["changed"] == ["company", "deal_value", "stage"]


def test_retry_after_an_uncertain_outcome_never_applies_twice(rig):
    token = admin(rig)
    creates = book([row(title="N1"), row(card_id=1, title="Renamed")])
    body = preview(rig, token, creates).json()
    assert confirm(rig, token, creates, body["plan_digest"]).status_code == 201  # the client never saw this response
    done = counts(rig)
    # 1. the blind retry of the same request
    retry = confirm(rig, token, creates, body["plan_digest"])
    assert retry.status_code == 409 and retry.json()["detail"]["code"] == "already_imported" and counts(rig) == done
    # 2. the explicit recheck: preview again and read the recorded result
    recheck = preview(rig, token, creates).json()
    assert recheck["already_imported"]["created"] == 1 and recheck["already_imported"]["updated"] == 1
    # 3. an update-only workbook is recognised from the ledger too, not from "nothing left to change"
    updates = book([row(card_id=2, title="Two")])
    body2 = preview(rig, token, updates).json()
    assert confirm(rig, token, updates, body2["plan_digest"]).status_code == 201
    done = counts(rig)
    retry2 = confirm(rig, token, updates, body2["plan_digest"])
    assert retry2.status_code == 409 and retry2.json()["detail"]["code"] == "already_imported"
    assert retry2.json()["detail"]["import_summary"]["updated"] == 1 and counts(rig) == done
    assert preview(rig, token, updates).json()["already_imported"]["updated"] == 1


def test_concurrent_confirmations_of_one_workbook_apply_it_once(rig):
    token = admin(rig)
    content = book([row(title="N1"), row(title="N2"), row(title="N3")])
    body = preview(rig, token, content).json()
    warm_up(rig, token)

    def request():
        return confirm(rig, token, content, body["plan_digest"])

    first, second = interleave(rig, request, request, r"INSERT INTO cards")
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["detail"]["code"] == "already_imported"
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(5,)]
    assert rig.sql("SELECT created_count FROM card_excel_imports") == [(3,)]


def test_concurrent_confirmations_of_one_update_only_workbook_apply_it_once(rig):
    token = admin(rig)
    content = book([row(card_id=1, title="Renamed once", company="Acme")])
    body = preview(rig, token, content).json()
    assert body["summary"] == {**body["summary"], "creates": 0, "updates": 1} and body["confirmable"] is True
    warm_up(rig, token)
    before_cards = cards(rig)

    def request():
        return confirm(rig, token, content, body["plan_digest"])

    # The first request is paused before its first UPDATE cards (it already holds the write lock); the second must
    # wait for that lock, then finds the committed ledger row and is refused before touching any card.
    first, second = interleave(rig, request, request, r"UPDATE cards")
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    loser = first if first.status_code == 409 else second
    assert loser.json()["detail"]["code"] == "already_imported"
    assert rig.sql("SELECT title, company FROM cards WHERE id = 1") == [("Renamed once", "Acme")]
    after_cards = cards(rig)
    assert [c for c in after_cards if c[0] != 1] == [c for c in before_cards if c[0] != 1]  # nothing else moved
    assert rig.sql("SELECT created_count, updated_count, unchanged_count FROM card_excel_imports") == [(0, 1, 0)]
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


def test_the_database_itself_refuses_a_second_guard_row(rig):
    token = admin(rig)
    assert apply(rig, token, book([row(title="N1")]))[1].status_code == 201
    fp = rig.sql("SELECT fingerprint FROM card_excel_imports")[0][0]
    with pytest.raises(sqlite3.IntegrityError):
        rig.sql("INSERT INTO card_excel_imports (board_id, filename, file_sha256, fingerprint, repeat_guard, created_count, "
                "updated_count, unchanged_count, changes_json, created_at) VALUES (1, 'x', 'x', ?, ?, 1, 0, 0, '{}', ?)",
                (fp, fp, NOW))


def test_the_database_guards_update_only_imports_too(rig):
    token = admin(rig)
    assert apply(rig, token, book([row(card_id=1, title="Only update")]))[1].status_code == 201
    fp = rig.sql("SELECT fingerprint FROM card_excel_imports")[0][0]
    assert rig.sql("SELECT repeat_guard FROM card_excel_imports") == [(fp,)]
    with pytest.raises(sqlite3.IntegrityError):
        rig.sql("INSERT INTO card_excel_imports (board_id, filename, file_sha256, fingerprint, repeat_guard, created_count, "
                "updated_count, unchanged_count, changes_json, created_at) VALUES (1, 'x', 'x', ?, ?, 0, 1, 0, '{}', ?)",
                (fp, fp, NOW))


def test_deleting_cards_does_not_release_a_created_workbook(rig):
    token = admin(rig)
    content = book([row(title="Gone soon")])
    assert apply(rig, token, content)[1].status_code == 201
    rig.sql("DELETE FROM cards WHERE title = 'Gone soon'")
    body = preview(rig, token, content).json()
    assert body["confirmable"] is False and body["blocked_reason"] == "already_imported"
    assert confirm(rig, token, content, body["plan_digest"]).status_code == 409
    assert rig.sql("SELECT COUNT(*) FROM cards WHERE title = 'Gone soon'") == [(0,)]


def test_confirmation_is_bound_to_the_preview(rig):
    token = admin(rig)
    content = book([row(card_id=1, title="Renamed"), row(title="N1")])
    body = preview(rig, token, content).json()
    snapshot = cards(rig)
    # wrong, missing and foreign digests
    assert confirm(rig, token, content, "0" * 64).status_code == 409
    assert confirm(rig, token, content, "").status_code == 409
    assert confirm(rig, token, book([row(title="Other")]), body["plan_digest"]).status_code == 409  # another workbook
    assert rig.call("post", CONFIRM, token, files=upload(content), data={"plan_digest": body["plan_digest"]}).status_code == 422
    assert cards(rig) == snapshot
    # the board changed after the preview
    rig.sql("UPDATE cards SET title = 'edited elsewhere', updated_at = '2030-01-01 00:00:00' WHERE id = 1")
    r = confirm(rig, token, content, body["plan_digest"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "preview_stale"
    assert rig.sql("SELECT title FROM cards WHERE id = 1") == [("edited elsewhere",)]
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


# --- authorization --------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [PREVIEW, CONFIRM])
@pytest.mark.parametrize("role", ["sales_member", "visitor"])
def test_members_and_visitors_are_refused_before_the_company_db_opens(rig, path, role):
    _, token = member(rig, role=role)
    before = rig.digest("lity")
    r = rig.call("post", path, token, files=upload(book([row(title="x")])), data={"confirm": "true", "plan_digest": "x"})
    assert r.status_code == 403 and rig.opened == [] and rig.digest("lity") == before


@pytest.mark.parametrize("path", [PREVIEW, CONFIRM])
def test_unauthenticated_and_garbage_tokens_are_401(rig, path):
    for token in (None, "garbage"):
        assert rig.call("post", path, token, files=upload(book([row(title="x")]))).status_code == 401
    assert rig.opened == []


def test_company_admin_cannot_import_and_the_super_admin_can_only_into_the_selected_company(rig):
    _, token = company_admin(rig)
    before = rig.digest("lity")
    rig.opened.clear()
    for path in (PREVIEW, CONFIRM):
        r = rig.call("post", path, token, files=upload(book([row(title="By admin")])), data={"confirm": "true", "plan_digest": "x"})
        assert r.status_code == 403
    assert rig.opened == [] and rig.digest("lity") == before
    sup = admin(rig)
    body, r = apply(rig, sup, book([row(title="By super admin")]))
    assert r.status_code == 201
    assert rig.sql("SELECT COUNT(*) FROM cards", slug="valor-log") == [(2,)]  # only its seeded cards
    assert rig.sql("SELECT user_id FROM card_excel_imports") == [(rig.e.admin,)]


def test_a_member_of_another_company_cannot_import(rig):
    _, token = member(rig, slug="valor-log")
    before = rig.digest("lity")
    assert rig.call("post", PREVIEW, token, files=upload(book([row(title="x")]))).status_code == 403
    assert rig.digest("lity") == before


def test_unknown_board_is_404_and_nothing_is_written(rig):
    token = admin(rig)
    r = rig.client.post("/api/companies/lity/boards/999/excel/import/preview",
                        headers={"Authorization": f"Bearer {token}"}, files=upload(book([row(title="x")])))
    assert r.status_code == 404


def test_company_admin_edits_of_others_cards_are_audited(rig):
    uid, token = company_admin(rig)
    rig.assign(1, 7, "ana")
    assert rig.call("put", "/cards/1", token, json={"title": "Admin edit"}).status_code == 200
    assert ("edit",) in rig.sql("SELECT action FROM audit_events")


# --- ledger availability and schema -----------------------------------------------------------------

def test_without_the_ledger_table_the_import_is_off(rig):
    token = admin(rig)
    rig.sql("DROP TABLE card_excel_imports")
    for path in (PREVIEW, CONFIRM):
        r = rig.call("post", path, token, files=upload(book([row(title="x")])), data={"confirm": "true", "plan_digest": "x"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "import_not_initialized"
        assert r.json()["detail"]["applied"] is False  # definite: refused before anything is read or written
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(2,)]


@pytest.mark.parametrize("ddl", [
    # an older build: no repeat_guard column, no unique guard
    "CREATE TABLE card_excel_imports (id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL, user_id INTEGER, filename VARCHAR(255) NOT NULL, "
    "file_sha256 VARCHAR(64) NOT NULL, fingerprint VARCHAR(64) NOT NULL, created_count INTEGER NOT NULL, updated_count INTEGER NOT NULL, "
    "unchanged_count INTEGER NOT NULL, changes_json TEXT NOT NULL, created_at DATETIME NOT NULL)",
    # all columns but no unique guard
    "CREATE TABLE card_excel_imports (id INTEGER PRIMARY KEY, board_id INTEGER NOT NULL, user_id INTEGER, filename VARCHAR(255) NOT NULL, "
    "file_sha256 VARCHAR(64) NOT NULL, fingerprint VARCHAR(64) NOT NULL, repeat_guard VARCHAR(64), created_count INTEGER NOT NULL, "
    "updated_count INTEGER NOT NULL, unchanged_count INTEGER NOT NULL, changes_json TEXT NOT NULL, created_at DATETIME NOT NULL)",
])
def test_a_ledger_of_an_unexpected_shape_fails_closed_before_anything_is_touched(rig, ddl):
    token = admin(rig)
    rig.sql("DROP TABLE card_excel_imports")
    rig.sql(ddl)
    before = cards(rig)
    for path in (PREVIEW, CONFIRM):
        r = rig.call("post", path, token, files=upload(book([row(title="x")])), data={"confirm": "true", "plan_digest": "x"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "import_ledger_incompatible"
        assert r.json()["detail"]["applied"] is False
    assert cards(rig) == before and rig.sql("SELECT COUNT(*) FROM card_excel_imports") == [(0,)]


def test_version_1_ledger_rows_do_not_match_version_2_fingerprints(rig):
    """A row recorded under another fingerprint version (a v1 hash) is not recognised by the v2 guard: the same
    logical workbook is then importable once more. Documented limitation; nothing rewrites such rows."""
    token = admin(rig)
    content = book([row(title="N1")])
    rig.sql("INSERT INTO card_excel_imports (board_id, filename, file_sha256, fingerprint, repeat_guard, created_count, updated_count, "
            "unchanged_count, changes_json, created_at) VALUES (1, 'old.xlsx', 'x', ?, NULL, 1, 0, 0, '{}', ?)", ("1" * 64, NOW))
    assert preview(rig, token, content).json()["confirmable"] is True


def test_new_company_databases_have_the_ledger_and_still_validate(rig):
    for slug in ("lity", "salcomp", "valor-log"):
        assert rig.sql("SELECT name FROM sqlite_master WHERE name = 'card_excel_imports'", slug=slug)
        assert rig.sql("PRAGMA user_version", slug=slug) == [(4,)]
        validate_company_schema(slug, rig.e.env)


def test_init_script_adds_the_ledger_to_an_existing_database_without_touching_anything_else(rig):
    rig.sql("DROP TABLE card_excel_imports")
    version_before, cards_before = rig.sql("PRAGMA user_version"), cards(rig)
    assert init_company_excel_imports("lity", False, rig.e.env) is True  # dry run reports, writes nothing
    assert not rig.sql("SELECT name FROM sqlite_master WHERE name = 'card_excel_imports'")
    assert init_company_excel_imports("lity", True, rig.e.env) is True
    assert rig.sql("SELECT name FROM sqlite_master WHERE name = 'card_excel_imports'")
    assert init_company_excel_imports("lity", True, rig.e.env) is False  # idempotent
    assert rig.sql("PRAGMA user_version") == version_before and cards(rig) == cards_before
    validate_company_schema("lity", rig.e.env)
    assert rig.sql("SELECT name FROM sqlite_master WHERE name = 'card_excel_imports'", slug="salcomp")  # others untouched
    token = admin(rig)
    assert apply(rig, token, book([row(title="after init")]))[1].status_code == 201


def test_init_script_is_company_scoped(rig):
    rig.sql("DROP TABLE card_excel_imports", slug="valor-log")
    rig.sql("DROP TABLE card_excel_imports", slug="salcomp")
    init_company_excel_imports("valor-log", True, rig.e.env)
    assert rig.sql("SELECT name FROM sqlite_master WHERE name = 'card_excel_imports'", slug="valor-log")
    assert not rig.sql("SELECT name FROM sqlite_master WHERE name = 'card_excel_imports'", slug="salcomp")


# --- round trip with the existing export -------------------------------------------------------------

def test_round_trip_export_import_updates_in_place_and_never_duplicates(rig):
    token = admin(rig)
    uid = person(rig)
    created = book([
        row(title="RT one", company="Acme", deal_value_usd=Decimal("1200.50"), deal_value_next_year_usd=Decimal("300"),
            confidence_level="MEDIUM", product_line="Chargers", salesperson_email="ana@example.com", stage="Second",
            expected_close_date=date(2026, 5, 6), lead_source="Trade show", details="notes", contact="Cara"),
        row(title="RT two"),
    ])
    assert apply(rig, token, created)[1].status_code == 201
    n_cards = rig.sql("SELECT COUNT(*) FROM cards")[0][0]
    snapshot = cards(rig)

    exported = rig.call("get", "/excel/export", token).content
    assert [c.value for c in load_workbook(io.BytesIO(exported)).worksheets[0][1]] == list(contract.HEADERS)

    # Policy: the exported card_id values are kept, so importing the export updates in place.
    body = preview(rig, token, exported).json()
    assert body["summary"] == {"rows": n_cards, "creates": 0, "updates": 0, "unchanged": n_cards, "invalid": 0,
                               "warnings": 1}
    assert [w["code"] for w in body["file_warnings"]] == ["unexpected_sheet"]  # the Instructions sheet is ignored
    assert body["confirmable"] is False and body["blocked_reason"] == "no_changes"
    assert confirm(rig, token, exported, body["plan_digest"]).status_code == 409
    assert cards(rig) == snapshot

    # Modify permitted fields (including an authoritative-looking total) and import again.
    wb = load_workbook(io.BytesIO(exported))
    ws = wb.worksheets[0]
    head = {c.value: c.column for c in ws[1]}
    target = next(r for r in range(2, ws.max_row + 1) if ws.cell(r, head["title"]).value == "RT one")
    ws.cell(target, head["title"]).value = "RT one (edited)"
    ws.cell(target, head["deal_value_usd"]).value = 2000.25
    ws.cell(target, head["deal_value_next_year_usd"]).value = None  # blank -> 0
    ws.cell(target, head["deal_total_usd"]).value = 999999  # ignored, derived
    ws.cell(target, head["deal_year"]).value = 1999  # ignored
    ws.cell(target, head["stage"]).value = "First"
    out = io.BytesIO()
    wb.save(out)
    edited = out.getvalue()
    body, r = apply(rig, token, edited)
    assert body["summary"]["updates"] == 1 and body["summary"]["creates"] == 0
    assert r.status_code == 201 and r.json()["updated"] == 1 and r.json()["created"] == 0
    assert rig.sql("SELECT COUNT(*) FROM cards") == [(n_cards,)]
    card = rig.sql("SELECT title, deal_value, deal_value_next_year, column_id, salesperson_id, product_line, company, "
                   "confidence_level, deal_year FROM cards WHERE title LIKE 'RT one%'")
    assert len(card) == 1
    assert card[0][:8] == ("RT one (edited)", 2000.25, 0.0, 1, uid, "Chargers", "Acme", "MEDIUM")
    assert card[0][8] == snapshot_year(snapshot, rig)
    # re-exporting shows the computed total, not the ignored one
    again = load_workbook(io.BytesIO(rig.call("get", "/excel/export", token).content)).worksheets[0]
    row_values = next(r for r in again.iter_rows(min_row=2, values_only=True) if r[1] == "RT one (edited)")
    assert row_values[head["deal_total_usd"] - 1] == 2000.25


def snapshot_year(snapshot, rig):
    return rig.sql("SELECT deal_year FROM cards WHERE id = 1")[0][0]  # every seeded card shares the creation year
